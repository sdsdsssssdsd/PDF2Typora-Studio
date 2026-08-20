"""Hardware probe smoke test."""

from utils.hardware_probe import format_hardware_summary, probe_hardware


def test_probe_hardware_does_not_raise():
    info = probe_hardware()
    summary = format_hardware_summary(info)
    assert "CPU" in summary
    assert "RAM" in summary
    assert "GPU" in summary
