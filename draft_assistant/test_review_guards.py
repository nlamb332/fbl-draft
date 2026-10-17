"""Regression checks from integration review."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from .contracts import DraftObservation, LeagueConfig, LivePlayer, RoomIdentity, utc_now
from .coordinator import Coordinator, ActionConflict
from .valuation import ALGORITHM_VERSION, _promotion_artifact_valid, rank_players, strategy_fingerprint
from .lookahead import apply_lookahead
def player(pid, eligibility=("G",), pts=10, rank=1):
    return LivePlayer(pid,pid,eligibility,{"PTS":pts},projected_gp=82,draft_rank=rank)
class ReviewGuardTests(unittest.TestCase):
    def setUp(self):
        self.cfg=LeagueConfig()
        self.room=RoomIdentity(self.cfg.league_id,self.cfg.season_id,self.cfg.team_id)
    def obs(self,sid="review",**kwargs):
        data=dict(session_id=sid,room=self.room,observed_at=utc_now(),revision=1,phase="draft",current_pick=1,
            on_clock_team=self.cfg.our_team,seconds_remaining=90,
            pick_order=(self.cfg.our_team,)+tuple(f"Opponent {i}" for i in range(13)),
            available_players=(player("flex",("C","G","F")),),complete=True,history_complete=True,autopick=False,
            rules={"teams":14,"rounds":10,"starting_slots":self.cfg.starting_slots,"bench_slots":4,"ir_slots":3,"scoring":self.cfg.scoring})
        data.update(kwargs)
        return DraftObservation(**data)
    def test_final_pick_legal_with_partial_pool(self):
        roster=(player("c",("C",)),player("f",("F",)))+tuple(player(f"g{i}") for i in range(7))
        pool=(player("illegal",pts=1000),player("legal",("F",),pts=1))
        for complete in (False,True):
            ranked=rank_players(self.obs(roster=roster,available_players=pool,available_pool_complete=complete),self.cfg)
            self.assertEqual([p.player_id for p in ranked],["legal"])
    def test_shared_eligibility_not_two_starters(self):
        roster=(player("cf",("C","F")),)+tuple(player(f"g{i}") for i in range(7))
        pool=(player("guard",pts=1000),player("f1",("F",)),player("f2",("F",)))
        for complete in (False,True):
            ranked=rank_players(self.obs(roster=roster,available_players=pool,available_pool_complete=complete),self.cfg)
            self.assertNotIn("guard",{p.player_id for p in ranked})
    def test_no_legal_final_pick_returns_empty(self):
        self.assertEqual(rank_players(self.obs(roster=tuple(player(f"g{i}") for i in range(9))),self.cfg),())
    def test_promotion_finite_and_bound_to_config(self):
        report={"algorithm_version":ALGORITHM_VERSION,"config_fingerprint":strategy_fingerprint(self.cfg),
            "validation":{"independent_seed_clusters":100,"teams":14,"opponents":13,"picks_per_draft":140,
                "seats_per_seed":14,"foundation_vs_lookahead":True,"paired_bootstrap_95_lower":1,"worst_seat_loss_fraction":-0.01}}
        self.assertTrue(_promotion_artifact_valid(report,self.obs(),self.cfg))
        self.assertFalse(_promotion_artifact_valid(report,self.obs(),replace(self.cfg,recommendation_budget_seconds=1)))
        for invalid in (float("nan"),float("inf"),-1):
            report["validation"]["paired_bootstrap_95_lower"]=invalid
            self.assertFalse(_promotion_artifact_valid(report,self.obs(),self.cfg))
    def test_partial_rollout_never_promotes(self):
        order=tuple(f"Opponent {i}" for i in range(13))+(self.cfg.our_team,)
        obs=self.obs(current_pick=14,pick_order=order,available_pool_complete=True,
            available_players=tuple(player(f"p{i}",("C","G","F"),rank=i+1) for i in range(12)))
        direct=rank_players(obs,self.cfg)
        with patch("draft_assistant.lookahead._rollout_score",return_value=(1000,False)) as rollout:
            result=apply_lookahead(obs,self.cfg,direct,promote=True)
        rollout.assert_called_once()
        self.assertEqual(result.status,"fallback")
        self.assertEqual(result.candidates,direct)
    def test_autopick_requires_explicit_resume(self):
        with Coordinator(":memory:",recover=False) as c:
            sid=c.create_session(self.room)["session_id"]
            c.ingest_observation(self.obs(sid));c.start(sid)
            c.ingest_observation(self.obs(sid,revision=2,autopick=True))
            self.assertEqual(c.status(sid)["state"],"paused")
            c.ingest_observation(self.obs(sid,revision=3))
            self.assertEqual(c.status(sid)["state"],"paused")
            c.resume(sid)
            self.assertEqual(c.status(sid)["state"],"running")
    def test_only_one_running_session(self):
        with Coordinator(":memory:",recover=False) as c:
            first=c.create_session(self.room)["session_id"]
            c.ingest_observation(self.obs(first));c.start(first)
            second=c.create_session(self.room)["session_id"]
            c.ingest_observation(self.obs(second))
            with self.assertRaises(ActionConflict):c.start(second)
            c.pause(first);c.start(second)
            self.assertEqual(c.status(second)["state"],"running")
