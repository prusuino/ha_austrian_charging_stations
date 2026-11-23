"""The favorite triggers with real automations, on a registered favorite
sensor whose state is set by hand: a change, a change during an outage, a
favorite that comes back unchanged."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.ladestellen_at import trigger
from custom_components.ladestellen_at.const import DOMAIN


def _favorite(hass: HomeAssistant, platform: str = DOMAIN, name: str = "favorite") -> str:
    return er.async_get(hass).async_get_or_create("sensor", platform, f"entry_{name}_status", suggested_object_id=name).entity_id


async def _play(hass: HomeAssistant, entity_id: str, *statuses: str) -> None:
    for status in statuses:
        if status in ("unavailable", "unknown"):
            hass.states.async_set(entity_id, status)
        else:
            hass.states.async_set(entity_id, status.capitalize(), {"site_status": status})
        await hass.async_block_till_done()


async def _automation(hass: HomeAssistant, name: str, entity_ids) -> list:
    calls = async_mock_service(hass, "test", name)
    assert await async_setup_component(hass, "automation", {"automation": {
        "alias": name, "trigger": {"trigger": f"ladestellen_at.{name}", "target": {"entity_id": entity_ids}},
        "action": {"action": f"test.{name}"},
    }})
    await hass.async_block_till_done()
    assert hass.states.get(f"automation.{name}") is not None, "the automation was rejected"
    return calls


async def test_became_available_and_other_integrations_are_ignored(hass: HomeAssistant, enable_custom_integrations) -> None:
    assert set(await trigger.async_get_triggers(hass)) == {"favorite_became_available", "favorite_became_occupied", "available_count_crossed_threshold"}
    favorite = _favorite(hass)
    foreign = _favorite(hass, "another_integration", "wallbox")
    await _play(hass, favorite, "occupied")
    await _play(hass, foreign, "occupied")
    calls = await _automation(hass, "favorite_became_available", [favorite, foreign])
    await _play(hass, foreign, "available")  # not ours
    assert len(calls) == 0
    await _play(hass, favorite, "available")
    assert len(calls) == 1
    await _play(hass, favorite, "occupied", "available")
    assert len(calls) == 2


async def test_a_change_during_an_outage_fires_and_an_unchanged_return_does_not(hass: HomeAssistant, enable_custom_integrations) -> None:
    favorite = _favorite(hass)
    await _play(hass, favorite, "occupied")
    calls = await _automation(hass, "favorite_became_available", favorite)
    await _play(hass, favorite, "unavailable", "available")  # occupied before the outage, free after it
    assert len(calls) == 1
    await _play(hass, favorite, "unavailable", "available")  # back unchanged
    assert len(calls) == 1
    await _play(hass, favorite, "out_of_service", "unknown", "available")
    assert len(calls) == 2


async def test_nothing_known_before_the_outage_stays_quiet(hass: HomeAssistant, enable_custom_integrations) -> None:
    favorite = _favorite(hass)
    await _play(hass, favorite, "unavailable")
    calls = await _automation(hass, "favorite_became_available", favorite)
    await _play(hass, favorite, "available")  # it may have been free all along
    assert len(calls) == 0
    await _play(hass, favorite, "occupied", "available")
    assert len(calls) == 1


async def test_became_occupied_after_an_outage(hass: HomeAssistant, enable_custom_integrations) -> None:
    favorite = _favorite(hass)
    await _play(hass, favorite, "available")
    calls = await _automation(hass, "favorite_became_occupied", favorite)
    await _play(hass, favorite, "unavailable", "occupied")
    assert len(calls) == 1
    await _play(hass, favorite, "unavailable", "occupied")
    assert len(calls) == 1
