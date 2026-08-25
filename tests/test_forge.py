"""Tests for the shared forge module: GraphQL query construction and the
GitHub/GitLab response normalizers, pinned end-to-end through rollup_checks
and pr_chip so glyph folding is covered against realistic payloads."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "_common"
    ),
)
import forge  # noqa: E402
import ui  # noqa: E402

ui.set_color(False)


def gh_node(number, state="OPEN", owner="me", is_draft=False, review=None, contexts=(),
            stack=None, stack_entry=None):
    return {
        "number": number,
        "state": state,
        "isDraft": is_draft,
        "baseRefName": "main",
        "url": f"https://example.test/pr/{number}",
        "title": f"PR {number}",
        "headRefOid": f"oid{number}",
        "reviewDecision": review,
        "headRepositoryOwner": {"login": owner},
        "stack": stack,
        "stackEntry": stack_entry,
        "commits": {
            "nodes": [
                {"commit": {"statusCheckRollup": {"contexts": {"nodes": list(contexts)}}}}
            ]
        },
    }


def gh_response(repo_owner="me", **aliases):
    repo = {"owner": {"login": repo_owner}}
    for alias, nodes in aliases.items():
        repo[alias] = {"nodes": list(nodes)}
    return {"data": {"repository": repo}}


class BuildGithubQueryTest(unittest.TestCase):
    def test_alias_map_covers_all_branches(self):
        q, aliases = forge.build_github_query(["a", "b", "c"])
        self.assertEqual(set(aliases.values()), {"a", "b", "c"})
        for alias in aliases:
            self.assertIn(f"{alias}: pullRequests", q)

    def test_branch_names_are_json_escaped(self):
        q, _ = forge.build_github_query(['we"ird\\name'])
        self.assertIn('headRefName: "we\\"ird\\\\name"', q)

    def test_chunks(self):
        self.assertEqual(forge._chunks(list(range(70)), 30),
                         [list(range(30)), list(range(30, 60)), list(range(60, 70))])
        self.assertEqual(forge._chunks([], 30), [])


class NormalizeGithubTest(unittest.TestCase):
    def test_prefers_open_over_newer_closed(self):
        data = gh_response(b0=[gh_node(5, "CLOSED"), gh_node(3, "OPEN")])
        prs = forge.normalize_github(data, {"b0": "feat"})
        self.assertEqual(prs["feat"]["number"], 3)
        self.assertEqual(prs["feat"]["state"], "OPEN")

    def test_prefers_highest_number_among_same_state(self):
        data = gh_response(b0=[gh_node(4, "MERGED"), gh_node(9, "MERGED")])
        prs = forge.normalize_github(data, {"b0": "feat"})
        self.assertEqual(prs["feat"]["number"], 9)

    def test_own_repo_head_beats_same_named_fork_branch(self):
        data = gh_response(
            b0=[gh_node(9, "OPEN", owner="someone-else"), gh_node(2, "CLOSED", owner="me")]
        )
        prs = forge.normalize_github(data, {"b0": "feat"})
        self.assertEqual(prs["feat"]["number"], 2)

    def test_branch_without_prs_maps_to_none(self):
        data = gh_response(b0=[])
        self.assertIsNone(forge.normalize_github(data, {"b0": "feat"})["feat"])

    def test_flattens_rollup_and_keeps_contract_fields(self):
        contexts = [
            {"__typename": "CheckRun", "name": "build", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS"},
        ]
        data = gh_response(b0=[gh_node(7, contexts=contexts, review="APPROVED")])
        pr = forge.normalize_github(data, {"b0": "feat"})["feat"]
        self.assertEqual(pr["statusCheckRollup"], contexts)
        for field in ("number", "state", "isDraft", "baseRefName", "url",
                      "title", "headRefOid", "reviewDecision"):
            self.assertIn(field, pr)
        self.assertEqual(forge.rollup_checks(pr), "SUCCESS")

    def test_end_to_end_chip_with_ignored_pending_check(self):
        ignored = "ci/slow-integration-suite"
        forge.set_ignored_when_pending({ignored})
        self.addCleanup(forge.set_ignored_when_pending, set())
        contexts = [
            {"__typename": "CheckRun", "name": "build", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"__typename": "CheckRun", "name": ignored, "conclusion": "", "status": "IN_PROGRESS"},
        ]
        pr = forge.normalize_github(
            gh_response(b0=[gh_node(7, contexts=contexts)]), {"b0": "feat"}
        )["feat"]
        self.assertEqual(forge.rollup_checks(pr), "SUCCESS")
        rendered, width = ui.pr_chip(pr)
        self.assertEqual(rendered, f"#7 {ui.SYM_CHECKS_PASS}")
        self.assertEqual(width, len("#7 ") + 1)

    def test_native_stack_membership_is_normalized(self):
        # Shape recorded from a real GitHub PR carrying a native stack.
        data = gh_response(b0=[gh_node(58011, stack={"number": 58215, "size": 2},
                                       stack_entry={"position": 1})])
        pr = forge.normalize_github(data, {"b0": "feat"})["feat"]
        self.assertEqual(pr["stack"], {"number": 58215, "size": 2, "position": 1})

    def test_stack_is_none_when_pr_not_in_a_stack(self):
        # Feature enabled, PR simply not stacked (recorded: PR #58724).
        data = gh_response(b0=[gh_node(58724, stack=None, stack_entry=None)])
        self.assertIsNone(forge.normalize_github(data, {"b0": "feat"})["feat"]["stack"])

    def test_stack_is_none_when_feature_disabled(self):
        # A repo without stacked PRs returns null for both fields rather than
        # erroring — this is what lets the query carry them unconditionally.
        data = gh_response(b0=[gh_node(1, stack=None, stack_entry=None)])
        pr = forge.normalize_github(data, {"b0": "feat"})["feat"]
        self.assertIsNone(pr["stack"])
        self.assertEqual(pr["number"], 1)  # everything else still normalizes

    def test_stack_query_fields_are_requested(self):
        q, _ = forge.build_github_query(["a"])
        self.assertIn("stack { number size }", q)
        self.assertIn("stackEntry { position }", q)

    def test_gitlab_prefix_renders_bang(self):
        pr = forge.normalize_github(gh_response(b0=[gh_node(7)]), {"b0": "feat"})["feat"]
        rendered, _ = ui.pr_chip(pr, num_prefix="!")
        self.assertTrue(rendered.startswith("!7 "))

    def test_chip_wraps_osc8_hyperlink_when_links_enabled(self):
        pr = forge.normalize_github(gh_response(b0=[gh_node(7)]), {"b0": "feat"})["feat"]
        plain, plain_w = ui.pr_chip(pr)
        ui.set_color(True)
        try:
            linked, linked_w = ui.pr_chip(pr)
        finally:
            ui.set_color(False)
        url = pr["url"]
        self.assertTrue(linked.startswith(f"\033]8;;{url}\033\\"))
        self.assertTrue(linked.endswith("\033]8;;\033\\"))
        self.assertEqual(linked_w, plain_w)
        # Links suppressed while disabled — no escape leaks into plain output.
        self.assertNotIn("\033]8;;", plain)


def gl_node(iid, state="opened", branch="feat", draft=False,
            approved=False, changes_requested=False, pipeline=None):
    return {
        "iid": str(iid),
        "state": state,
        "draft": draft,
        "sourceBranch": branch,
        "targetBranch": "main",
        "webUrl": f"https://gitlab.test/mr/{iid}",
        "title": f"MR {iid}",
        "diffHeadSha": f"sha{iid}",
        "approvedBy": {"nodes": [{"username": "rev"}] if approved else []},
        "reviewers": {
            "nodes": [
                {"mergeRequestInteraction": {"reviewState": "REQUESTED_CHANGES"}}
            ] if changes_requested else []
        },
        "headPipeline": {"status": pipeline} if pipeline else None,
    }


def gl_response(*nodes):
    return {"data": {"project": {"mergeRequests": {"nodes": list(nodes)}}}}


class NormalizeGitlabTest(unittest.TestCase):
    def test_prefers_opened_then_highest_iid_numerically(self):
        # "10" < "9" as strings — the sort must compare iids as ints.
        prs = forge.normalize_gitlab(gl_response(
            gl_node(9, "opened"), gl_node(10, "opened"), gl_node(12, "closed"),
        ))
        self.assertEqual(prs["feat"]["number"], 10)
        self.assertEqual(prs["feat"]["state"], "OPEN")

    def test_state_mapping(self):
        prs = forge.normalize_gitlab(gl_response(
            gl_node(1, "merged", branch="a"), gl_node(2, "closed", branch="b"),
        ))
        self.assertEqual(prs["a"]["state"], "MERGED")
        self.assertEqual(prs["b"]["state"], "CLOSED")

    def test_stack_key_present_and_none(self):
        """GitLab has no native stacks, but the dict shape must match GitHub's
        so consumers never branch on the forge."""
        prs = forge.normalize_gitlab(gl_response(gl_node(1)))
        self.assertIn("stack", prs["feat"])
        self.assertIsNone(prs["feat"]["stack"])

    def test_review_decision(self):
        prs = forge.normalize_gitlab(gl_response(
            gl_node(1, branch="a", approved=True),
            gl_node(2, branch="b", changes_requested=True),
            gl_node(3, branch="c"),
        ))
        self.assertEqual(prs["a"]["reviewDecision"], "APPROVED")
        self.assertEqual(prs["b"]["reviewDecision"], "CHANGES_REQUESTED")
        self.assertIsNone(prs["c"]["reviewDecision"])

    def test_pipeline_folding(self):
        cases = {
            "SUCCESS": "SUCCESS", "FAILED": "FAILURE",
            "RUNNING": "PENDING", "CANCELED": "NONE", None: "NONE",
        }
        for status, expected in cases.items():
            pr = forge.normalize_gitlab(gl_response(gl_node(1, pipeline=status)))["feat"]
            self.assertEqual(forge.rollup_checks(pr), expected, status)

    def test_query_inlines_and_escapes_branches(self):
        q = forge.build_gitlab_query("group/proj", ['a"b'])
        self.assertIn('sourceBranches: ["a\\"b"]', q)
        self.assertIn('project(fullPath: "group/proj")', q)
        # GitLab's MergeRequest exposes the head SHA as diffHeadSha, not sha;
        # a bare `sha` fails the whole query.
        self.assertIn("diffHeadSha", q)


class BranchPrChipTest(unittest.TestCase):
    """The shared per-branch chip both stack and wt render once a fetch
    succeeds: PR chip / pushed-no-PR dash / local-only net-off."""

    def test_pr_delegates_to_pr_chip(self):
        pr = forge.normalize_github(gh_response(b0=[gh_node(7)]), {"b0": "feat"})["feat"]
        chip = ui.branch_pr_chip(pr, True, ui.SYM_GITHUB)
        self.assertEqual(chip, ui.pr_chip(pr, "#", ui.SYM_GITHUB))

    def test_pushed_no_pr_is_dim_dash(self):
        # Plain mode: no forge icon, so the chip is just the dash.
        rendered, width = ui.branch_pr_chip(None, True, ui.SYM_GITHUB)
        self.assertEqual(rendered, "–")
        self.assertEqual(width, 1)

    def test_pushed_no_pr_keeps_a_nerd_font_icon(self):
        rendered, width = ui.branch_pr_chip(None, True, "\uf09b")
        self.assertEqual(rendered, "\uf09b –")
        self.assertEqual(width, 3)

    def test_local_only_is_net_off(self):
        rendered, width = ui.branch_pr_chip(None, False, ui.SYM_GITHUB)
        self.assertEqual(rendered, ui.SYM_NET_OFF)
        self.assertEqual(width, 1)


class ForgeDetectionTest(unittest.TestCase):
    """The pure URL sniffers shared by stack and wt."""

    def test_kind_from_ssh_urls(self):
        self.assertEqual(forge.forge_kind("git@github.com:o/r.git"), "github")
        self.assertEqual(forge.forge_kind("git@gitlab.com:g/s/r.git"), "gitlab")

    def test_kind_from_https_urls(self):
        self.assertEqual(forge.forge_kind("https://github.com/o/r.git"), "github")
        self.assertEqual(forge.forge_kind("https://gitlab.example.com/g/r"), "gitlab")

    def test_kind_unknown_host_and_empty(self):
        self.assertEqual(forge.forge_kind("git@bitbucket.org:o/r.git"), "none")
        self.assertEqual(forge.forge_kind(""), "none")
        self.assertEqual(forge.forge_kind(None), "none")

    def test_kind_override_wins(self):
        # A self-hosted host the sniff can't classify, pinned via *.forge config.
        self.assertEqual(forge.forge_kind("git@git.corp:o/r.git", "gitlab"), "gitlab")

    def test_fullpath_ssh_and_https(self):
        self.assertEqual(forge.gitlab_fullpath("git@gitlab.com:g/s/r.git"), "g/s/r")
        self.assertEqual(forge.gitlab_fullpath("https://gitlab.com/g/r/"), "g/r")

    def test_fullpath_unresolvable(self):
        self.assertIsNone(forge.gitlab_fullpath(""))
        self.assertIsNone(forge.gitlab_fullpath(None))


class ResolvePrStateTest(unittest.TestCase):
    def test_populated_box_is_on(self):
        prs = {"main": {"number": 1}}
        self.assertEqual(forge.resolve_pr_state({"prs": prs}), ("on", prs))

    def test_failed_fetch_is_error(self):
        self.assertEqual(forge.resolve_pr_state({"prs": None}), ("error", {}))


if __name__ == "__main__":
    unittest.main()
