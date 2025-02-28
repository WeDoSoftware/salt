"""
tests.pytests.integration.reactor.test_reactor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Test Salt's reactor system
"""

import logging
import pathlib
import time

import pytest
import salt.utils.event
import salt.utils.reactor
from salt.serializers import yaml
from tests.support.helpers import PRE_PYTEST_SKIP_REASON

pytestmark = [
    pytest.mark.slow_test,
    pytest.mark.windows_whitelisted,
]

log = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def event_listener(salt_factories):
    return salt_factories.event_listener


@pytest.fixture
def master_event_bus(salt_master):
    with salt.utils.event.get_master_event(
        salt_master.config.copy(),
        salt_master.config["sock_dir"],
        listen=True,
        raise_errors=True,
    ) as event:
        yield event


def test_ping_reaction(event_listener, salt_minion):
    """
    Fire an event on the master and ensure that it pings the minion
    """
    event_tag = "/test_event"
    start_time = time.time()
    # Create event bus connection
    with salt.utils.event.get_event(
        "minion",
        sock_dir=salt_minion.config["sock_dir"],
        opts=salt_minion.config.copy(),
    ) as event:
        event.fire_event({"a": "b"}, event_tag)

    event_pattern = (salt_minion.id, event_tag)
    matched_events = event_listener.wait_for_events(
        [event_pattern], after_time=start_time, timeout=90
    )
    assert matched_events.found_all_events
    for event in matched_events:
        assert event.data == {"a": "b"}


@pytest.mark.skip_on_windows(reason=PRE_PYTEST_SKIP_REASON)
def test_reactor_reaction(
    event_listener, salt_master, salt_minion, master_event_bus, reactor_event
):
    """
    Fire an event on the master and ensure the reactor event responds
    """

    start_time = time.time()
    master_event_bus.fire_event({"id": salt_minion.id}, reactor_event.tag)
    event_pattern = (salt_master.id, reactor_event.event_tag)
    matched_events = event_listener.wait_for_events(
        [event_pattern], after_time=start_time, timeout=90
    )
    assert matched_events.found_all_events
    for event in matched_events:
        assert event.data["test_reaction"] is True


@pytest.mark.skip_on_windows(reason=PRE_PYTEST_SKIP_REASON)
def test_reactor_is_leader(
    event_listener,
    salt_master,
    salt_run_cli,
    master_event_bus,
    reactor_event,
    salt_minion,
):
    """
    If reactor system is unavailable, an exception is thrown.
    When leader is true (the default), the reacion event should return.
    When leader is set to false reactor should timeout/not do anything.
    """
    ret = salt_run_cli.run("reactor.is_leader")
    assert ret.exitcode == 0
    assert (
        "salt.exceptions.CommandExecutionError: Reactor system is not running."
        in ret.stdout
    )

    ret = salt_run_cli.run("reactor.set_leader", value=True)
    assert ret.exitcode == 0
    assert (
        "salt.exceptions.CommandExecutionError: Reactor system is not running."
        in ret.stdout
    )

    ret = salt_run_cli.run("reactor.is_leader")
    assert ret.exitcode == 0
    assert (
        "salt.exceptions.CommandExecutionError: Reactor system is not running."
        in ret.stdout
    )

    # make reactor not the leader; ensure reactor engine is available
    engines_config = salt_master.config.get("engines").copy()
    for idx, engine in enumerate(list(engines_config)):
        if "reactor" in engine:
            engines_config.pop(idx)

    engines_config.append(
        {
            "reactor": {
                "refresh_interval": 60,
                "worker_threads": 10,
                "worker_hwm": 10000,
            }
        }
    )
    config_overrides = yaml.serialize({"engines": engines_config})
    confd_dir = (
        pathlib.Path(salt_master.config_dir)
        / pathlib.Path(salt_master.config["default_include"]).parent
    )
    confd_dir.mkdir(exist_ok=True)

    # Now, with the temp config in place, ensure the reactor engine is running
    with pytest.helpers.temp_file("reactor-test.conf", config_overrides, confd_dir):
        ret = salt_run_cli.run("reactor.set_leader", value=True)
        assert ret.exitcode == 0
        assert (
            "CommandExecutionError" not in ret.stdout
        ), "reactor engine is not running"

        ret = salt_run_cli.run("reactor.is_leader")
        assert ret.exitcode == 0
        assert ret.stdout.endswith("\ntrue\n")

        ret = salt_run_cli.run("reactor.set_leader", value=False)
        assert ret.exitcode == 0

        ret = salt_run_cli.run("reactor.is_leader")
        assert ret.exitcode == 0
        assert ret.stdout.endswith("\nfalse\n")

        start_time = time.time()
        log.warning("START\n\n\n")
        master_event_bus.fire_event({"id": salt_minion.id}, reactor_event.tag)

        # Since leader is false, let's try and get the fire event to ensure it was triggered
        event_pattern = (salt_master.id, reactor_event.tag)
        matched_events = event_listener.wait_for_events(
            [event_pattern], after_time=start_time, timeout=90
        )
        assert matched_events.found_all_events
        # Now that we matched the trigger event, let's confirm we don't get the reaction event
        event_pattern = (salt_master.id, reactor_event.event_tag)
        matched_events = event_listener.wait_for_events(
            [event_pattern], after_time=start_time, timeout=30
        )
        assert matched_events.found_all_events is not True

        # make reactor the leader again; ensure reactor engine is available
        ret = salt_run_cli.run("reactor.set_leader", value=True)
        assert ret.exitcode == 0
        ret = salt_run_cli.run("reactor.is_leader")
        assert ret.exitcode == 0
        assert ret.stdout.endswith("\ntrue\n")

        # trigger a reaction
        start_time = time.time()
        master_event_bus.fire_event({"id": salt_minion.id}, reactor_event.tag)
        event_pattern = (salt_master.id, reactor_event.event_tag)
        matched_events = event_listener.wait_for_events(
            [event_pattern], after_time=start_time, timeout=90
        )
        assert matched_events.found_all_events
        for event in matched_events:
            assert event.data["test_reaction"] is True

    # Let's just confirm the engine is not running once again(because the config file is deleted by now)
    ret = salt_run_cli.run("reactor.is_leader")
    assert ret.exitcode == 0
    assert (
        "salt.exceptions.CommandExecutionError: Reactor system is not running."
        in ret.stdout
    )
