import tempfile
import unittest
from pathlib import Path

from tools import build_docs


class DocsBuildTests(unittest.TestCase):
    def test_schema_reference_is_generated_from_schema_sql(self):
        md = build_docs.generate_schema_reference(Path("schema.sql"))

        self.assertIn("Source: [schema.sql]", md)
        self.assertIn("## attempts", md)
        self.assertIn("| parameters | TEXT | no |", md)
        self.assertIn("idx_attempts_session", md)

    def test_config_reference_includes_skills_scheduler_and_suppressions(self):
        md = build_docs.generate_config_reference(Path("."))

        self.assertIn("Source: [skills.yaml]", md)
        self.assertIn("Source: [suppressions.yaml]", md)
        self.assertIn("[server/suppressions.py]", md)
        self.assertIn("weather_math", md)
        self.assertIn("trivial_diff", md)
        self.assertIn("weather: temp_delta, daily_range, f_to_c_approx, wind_delta", md)

    def test_decision_flows_reference_code_and_mermaid(self):
        md = build_docs.generate_decision_flows(Path("docs/reference/decision-flows.yaml"))

        self.assertIn("```mermaid", md)
        self.assertIn("server/scheduler.py", md)
        self.assertIn("server/generator.py", md)
        self.assertIn("suppressions.yaml", md)
        self.assertIn("build_session_plan", md)

    def test_public_docs_privacy_scan_rejects_sensitive_terms(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "index.html").write_text("FEYNMAN_PLAID_LATEST_JSON")

            with self.assertRaises(build_docs.PrivacyLeakError):
                build_docs.assert_public_docs_safe(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "index.html").write_text("https://example.test/server/money.py")

            with self.assertRaises(build_docs.PrivacyLeakError):
                build_docs.assert_public_docs_safe(root)

    def test_docs_workflow_uses_single_build_and_watches_sources(self):
        workflow = Path(".github/workflows/docs.yml").read_text()

        self.assertIn("python tools/build_docs.py", workflow)
        self.assertIn("tools/build_docs.py", workflow)
        self.assertIn("schema.sql", workflow)
        self.assertIn("suppressions.yaml", workflow)
        self.assertIn("docs/reference/decision-flows.yaml", workflow)
        self.assertNotIn("cat > site/index.html", workflow)


if __name__ == "__main__":
    unittest.main()
