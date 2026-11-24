"""The call cap must be shorter than the deregistration delay. CloudFormation can't compare two parameters,
so the calling task checks it at startup and exits before it protects itself or takes a call."""
import pytest

import calling_task as ct


@pytest.mark.parametrize("cap, delay, ok", [(600, 900, True), (90, 180, True), (900, 900, False), (901, 900, False)])
def test_config_error(monkeypatch, cap, delay, ok):
    monkeypatch.setattr(ct, "CALL_CAP_SECONDS", cap)
    monkeypatch.setattr(ct, "DEREGISTRATION_DELAY_SECONDS", delay)
    error = ct.config_error()
    assert (error is None) is ok
    if not ok:
        assert "must be shorter than DEREGISTRATION_DELAY_SECONDS" in error


async def test_main_exits_before_doing_anything_when_misconfigured(monkeypatch):
    monkeypatch.setattr(ct, "CALL_CAP_SECONDS", 900)
    monkeypatch.setattr(ct, "DEREGISTRATION_DELAY_SECONDS", 600)
    logged, touched = [], []
    monkeypatch.setattr(ct, "log", lambda e, **f: logged.append(e))
    monkeypatch.setattr(ct, "task_metadata", lambda: touched.append("metadata"))
    monkeypatch.setattr(ct, "set_protection", lambda en: touched.append("protect"))
    with pytest.raises(SystemExit) as exc:
        await ct.main()
    assert exc.value.code == 2
    assert logged == ["config_invalid"] and touched == [], "the task did work before exiting"
