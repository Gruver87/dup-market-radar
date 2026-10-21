from radar.notify.webhook import WebhookNotifier, _host_ok


def test_discord_host_ok():
    assert _host_ok("https://discord.com/api/webhooks/1/abc")
    assert _host_ok("https://discordapp.com/api/webhooks/1/abc")
    assert not _host_ok("http://discord.com/api/webhooks/1/abc")
    assert not _host_ok("https://evil.example/hook")


def test_notifier_refuses_bad_host():
    n = WebhookNotifier("https://evil.example/hook")
    assert not n.configured()
    assert n.send_text("hi") is False
    assert n.status()["refused"] >= 1
