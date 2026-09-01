#!/usr/bin/env python3
"""End-to-end tests: local_agent.py driven as a subprocess against fakes.

Run with:  python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Session                                        # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = Session(self.tmp)
        self.s.__enter__()
        self.addCleanup(self.s.__exit__, None, None, None)


class SmokeTest(Base):
    def test_ps_talks_to_ollama(self):
        r = self.s.run("ps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing loaded", r.stdout)

    def test_a_single_run_reaches_the_launcher(self):
        self.s.plan([{"result": "wrote it", "session_id": "s1"}])
        r = self.s.run("run", "code", "do a thing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.s.calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0]["model"], "local-code")
        self.assertIn("do a thing", calls[0]["prompt"])


class PermissionTest(Base):
    def test_write_mode_gets_full_authority_and_the_deny_list(self):
        self.s.plan([{}])
        self.s.run("run", "code", "task")
        call = self.s.calls()[0]
        self.assertEqual(call["permission_mode"], "bypassPermissions")
        self.assertIn("Bash(git push *)", call["disallowed"])
        self.assertIn("WebFetch", call["disallowed"])

    def test_read_only_mode_cannot_edit(self):
        self.s.plan([{}])
        self.s.run("run", "--ro", "code", "analyse this")
        self.assertEqual(self.s.calls()[0]["permission_mode"], "plan")

    def test_the_api_key_is_stripped_and_ollama_substituted(self):
        self.s.plan([{}])
        self.s.run("run", "code", "task", ANTHROPIC_API_KEY="sk-should-not-survive")
        env = self.s.calls()[0]["env"]
        self.assertIsNone(env["ANTHROPIC_API_KEY"])
        self.assertEqual(env["ANTHROPIC_BASE_URL"], self.s.ollama.url)


class GuardrailsTest(Base):
    """The system prompt is the whole of the agent's instructions about how to work.

    It was defined and never passed for a while, which is invisible from the outside:
    runs still succeed, they just take none of the guidance.
    """

    def test_the_guardrails_reach_the_child(self):
        self.s.plan([{}])
        self.s.run("run", "code", "task")
        raw = " ".join(self.s.calls()[0]["raw"])
        self.assertIn("--append-system-prompt", raw)
        self.assertIn("YOUR TOKENS ARE FREE", raw)
        self.assertIn("verify it", raw, "the API-invention ban must be in there")


class OutsideDirTest(Base):
    def test_an_added_directory_is_readable_but_not_writable(self):
        """`--add-dir` follows the current permission mode, so under bypassPermissions
        it would otherwise grant writes outside the repository."""
        outside = self.tmp / "library"
        outside.mkdir()
        self.s.plan([{}])
        self.s.run("run", "code", "task", "--add-dir", str(outside))
        call = self.s.calls()[0]
        self.assertIn(str(outside), " ".join(call["add_dir"]))
        edit_rules = [d for d in call["disallowed"] if d.startswith("Edit(")]
        self.assertTrue(edit_rules, f"expected an Edit deny rule; got {call['disallowed']}")
        self.assertIn("library", edit_rules[0])


class InstallCarveOutTest(Base):
    def test_installs_are_denied_by_default(self):
        self.s.plan([{}])
        self.s.run("run", "code", "task")
        self.assertIn("Bash(pip install *)", self.s.calls()[0]["disallowed"])

    def test_allow_installs_lifts_only_the_install_rules(self):
        self.s.plan([{}])
        self.s.run("run", "code", "task", "--allow-installs")
        denied = self.s.calls()[0]["disallowed"]
        self.assertNotIn("Bash(pip install *)", denied)
        self.assertNotIn("Bash(npm install *)", denied)
        self.assertIn("Bash(git push *)", denied, "the rest of the net must hold")
        self.assertIn("Bash(sudo *)", denied)


class RefusalTest(Base):
    def test_a_dirty_tree_refuses_with_a_nonzero_exit(self):
        (self.s.repo / "seed.txt").write_text("changed\n", encoding="utf-8")
        r = self.s.run("run", "code", "task")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.s.calls(), [], "nothing should have been launched")

    def test_a_missing_model_refuses_with_a_nonzero_exit(self):
        r = self.s.run("run", "vision", "task")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("missing", (r.stdout + r.stderr).lower())


class FlushTest(Base):
    """The queue is the skill's flagship workflow; these are its documented shapes."""

    def test_two_write_tasks_in_one_flush_both_run(self):
        """SKILL.md's worked example queues two `code` tasks back to back.

        The first legitimately dirties the tree. If the clean-tree precondition is
        re-checked per task rather than per batch, the second is refused and the
        documented workflow can never succeed.
        """
        self.s.plan([
            {"write": {"path": "a.txt", "content": "one"}, "session_id": "s1"},
            {"write": {"path": "b.txt", "content": "two"}, "session_id": "s1"},
        ])
        self.s.run("queue", "code", "first task")
        self.s.run("queue", "code", "second task")
        r = self.s.run("flush")

        calls = self.s.calls()
        self.assertEqual(len(calls), 2,
                         f"both tasks must run; stdout:\n{r.stdout}\n{r.stderr}")
        self.assertTrue((self.s.repo / "a.txt").is_file())
        self.assertTrue((self.s.repo / "b.txt").is_file())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_flush_that_could_not_start_keeps_the_queue(self):
        """Ollama is up but the model is missing: nothing ran, so nothing is lost."""
        self.s.run("queue", "vision", "needs a model that is not installed")
        r = self.s.run("flush")
        self.assertNotEqual(r.returncode, 0)
        listed = self.s.run("list")
        self.assertIn("needs a model", listed.stdout,
                      "a task that never started must stay queued")

    def test_groups_run_heaviest_first(self):
        self.s.plan([{}, {}, {}])
        self.s.run("queue", "tiny", "tiny task")
        self.s.run("queue", "code", "code task")
        self.s.run("queue", "text", "text task")
        self.s.run("flush")
        models = [c["model"] for c in self.s.calls()]
        self.assertEqual(models, ["local-code", "local-text", "local-tiny"])

    def test_a_partial_failure_splits_the_queue(self):
        """What ran is archived; what never started stays ready to retry."""
        self.s.plan([{}])
        self.s.run("queue", "code", "this one runs")
        self.s.run("queue", "vision", "this one has no model")
        r = self.s.run("flush")
        self.assertNotEqual(r.returncode, 0)
        listed = self.s.run("list").stdout
        self.assertIn("no model", listed)
        self.assertNotIn("this one runs", listed)

    def test_the_model_stays_resident_after_a_flush(self):
        """Evicting what was just loaded makes the next delegation pay a full reload."""
        self.s.plan([{}])
        self.s.run("queue", "code", "task")
        self.s.run("flush")
        keeps = [c for c in self.s.ollama.calls
                 if c["path"] == "/api/generate" and c["payload"].get("keep_alive")
                 not in (0, None)]
        self.assertTrue(keeps, "expected a keep_alive refresh after the flush")
        self.assertEqual(keeps[-1]["payload"]["model"], "local-code")

    def test_new_files_appear_in_the_summary(self):
        """A created module is the commonest delegated shape; it must not be invisible."""
        self.s.plan([{"write": {"path": "src/made.ts", "content": "export const x = 1;\n"}}])
        self.s.run("queue", "code", "create the module")
        r = self.s.run("flush")
        self.assertIn("src/made.ts", r.stdout)

    def test_the_tool_own_state_does_not_leak_into_the_summary(self):
        """Files under .local-delegate/ (sessions.json, verify.ps1, logs) are the tool's
        own state, not the agent's work, and must not pollute the diff a reviewer trusts."""
        self.s.plan([{"write": {"path": "src/made.ts", "content": "export const x = 1;\n"}}])
        self.s.run("queue", "code", "create the module")
        r = self.s.run("flush")
        self.assertIn("src/made.ts", r.stdout)
        # Check the new-files section only: everything after the last "── N new file(s)"
        # header through the end of stdout.  The log path prints ".local-delegate" too,
        # so the assertion must be scoped to the section we are fixing.
        section = r.stdout.split("── ")[-1]
        self.assertNotIn(".local-delegate", section)


class VerifyTest(Base):
    def test_a_passing_verification_stops_after_one_attempt(self):
        self.s.plan([{}, {}, {}])
        ok = f'"{sys.executable}" -c "raise SystemExit(0)"'
        r = self.s.run("run", "code", "task", "--verify", ok)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.s.calls()), 1)

    def test_a_failing_verification_retries_locally_then_reports_failure(self):
        """Local tokens are free, so the loop runs here rather than costing a round trip."""
        self.s.plan([{}, {}, {}])
        bad = f'"{sys.executable}" -c "raise SystemExit(1)"'
        r = self.s.run("run", "code", "task", "--verify", bad, "--attempts", "3")
        self.assertEqual(len(self.s.calls()), 3, "should have retried up to the cap")
        self.assertNotEqual(r.returncode, 0, "an unverified task must not report success")
        self.assertIn("NOT confirmed", r.stdout + r.stderr)


class VerifyShellTest(Base):
    def test_a_failing_command_is_detected_whatever_the_shell(self):
        """PowerShell returns 0 for the script unless the native exit code is
        forwarded, so a verification step could report success on every failure."""
        self.s.plan([{}, {}, {}])
        failing = f'"{sys.executable}" -c "raise SystemExit(3)"'
        r = self.s.run("run", "code", "task", "--verify", failing, "--attempts", "1")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT confirmed", r.stdout + r.stderr)

    def test_a_passing_command_is_detected_whatever_the_shell(self):
        self.s.plan([{}])
        passing = f'"{sys.executable}" -c "print(1)"'
        r = self.s.run("run", "code", "task", "--verify", passing, "--attempts", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SessionTest(Base):
    def test_a_second_task_resumes_the_first_ones_session(self):
        """Fifteen cold starts was the measured cost of one-shot invocation."""
        self.s.plan([{"session_id": "sess-abc"}, {"session_id": "sess-abc"}])
        self.s.run("run", "code", "first")
        self.s.run("run", "code", "second")
        calls = self.s.calls()
        self.assertIsNone(calls[0].get("resume"), "the first call starts fresh")
        self.assertEqual(calls[1].get("resume"), "sess-abc")

    def test_fresh_ignores_the_stored_session(self):
        self.s.plan([{"session_id": "sess-abc"}, {"session_id": "sess-abc"}])
        self.s.run("run", "code", "first")
        self.s.run("run", "--fresh", "code", "second")
        self.assertIsNone(self.s.calls()[1].get("resume"))

    def test_a_stale_session_is_dropped_and_the_run_retried(self):
        """A stored id outlives its session when the local config is cleared.

        Without the fallback the profile is stuck: every run resumes an id that no
        longer resolves, and the caller has no reason to suspect it needs --fresh.
        """
        self.s.plan([
            {"session_id": "sess-gone"},
            {"result": "No conversation found with session ID sess-gone", "exit": 1},
            {"result": "started over", "session_id": "sess-new"},
        ])
        self.s.run("run", "code", "first")
        r = self.s.run("run", "code", "second")
        calls = self.s.calls()
        self.assertEqual(len(calls), 3, "the failed resume should be retried")
        self.assertEqual(calls[1].get("resume"), "sess-gone")
        self.assertIsNone(calls[2].get("resume"), "the retry must not resume")
        self.assertEqual(r.returncode, 0)


class ProjectRulesTest(Base):
    def test_repository_rules_reach_the_child(self):
        (self.s.repo / "AGENTS.md").write_text(
            "Always write measured, never estimated.\n", encoding="utf-8")
        self.s.git("add", "-A")
        self.s.git("commit", "-qm", "rules")
        self.s.plan([{}])
        self.s.run("run", "code", "task")
        self.assertIn("Always write measured", self.s.calls()[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
