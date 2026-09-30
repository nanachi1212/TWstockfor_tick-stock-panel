from pathlib import Path


def test_social_sentiment_scheduler_has_two_hidden_start_when_available_tasks():
    script = (
        Path(__file__).parents[2] / "scripts" / "install-social-sentiment-schedule.ps1"
    ).read_text(encoding="utf-8")

    assert "08:30" in script
    assert "15:30" in script
    assert "-StartWhenAvailable" in script
    assert "-Hidden" in script
    assert "-MultipleInstances IgnoreNew" in script
    assert "-NoProfile -NonInteractive -WindowStyle Hidden" in script
    assert "-Trigger PreOpen" in script
    assert "-Trigger AfterClose" in script
    assert 'Unregister-ScheduledTask -TaskName $RecoveryTaskName' in script
    assert "09:30" not in script
