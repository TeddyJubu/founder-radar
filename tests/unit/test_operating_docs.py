from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def test_operator_pages_explain_qa_and_signal_discovery():
    architecture = (ROOT / "architecture.html").read_text()
    onboarding = (ROOT / "prototype/onboarding.html").read_text()
    assert "Today QA" in architecture
    assert "live review UI" in architecture
    assert "SH01 is not proof of outside investment" in architecture
    assert "signal sources" in onboarding
    assert "public-release-policy.md" not in (ROOT / "README.md").read_text()
