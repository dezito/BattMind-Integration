"""Which actions of the blueprint automation have steps: read from automations.yaml, never written.

BattMind can not see what an automation does, but an automation made from the blueprint has its inputs in the file the
automation editor writes. The entity of an automation does not help: for a blueprint automation its raw_config is the
substituted configuration, without use_blueprint and its inputs. Automations kept in other files (packages, YAML mode)
are not found, and the test then does not know: it never reads that as "no steps".
"""
from __future__ import annotations

import logging
import os

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import yaml as yaml_util

_LOGGER = logging.getLogger(__name__)

BLUEPRINT_PATH = "battmind/battery_control.yaml"
ACTION_INPUT = "battmind_action"


def _blueprint_input(automation: object, action_sensor: str) -> dict | None:
    """The inputs of an automation that uses the BattMind blueprint for action_sensor, else None."""
    blueprint = automation.get("use_blueprint") if isinstance(automation, dict) else None
    if not isinstance(blueprint, dict) or blueprint.get("path") != BLUEPRINT_PATH or not isinstance(blueprint.get("input"), dict):
        return None
    return blueprint["input"] if blueprint["input"].get(ACTION_INPUT) == action_sensor else None


def automations_using_blueprint(path: str, action_sensor: str) -> list[dict]:
    """The inputs of the automations in the file at path that use the BattMind blueprint for action_sensor.

    Blocking file I/O. [] when the file is missing or unreadable or holds no such automation."""
    if not os.path.isfile(path):
        return []
    try:
        automations = yaml_util.load_yaml(path)
    except (HomeAssistantError, OSError) as err:
        _LOGGER.debug("Could not read %s to see which actions the automation has steps for: %s", path, err)
        return []
    found: list[dict] = []
    for automation in automations if isinstance(automations, list) else []:
        inputs = _blueprint_input(automation, action_sensor)
        if inputs is not None:
            found.append(inputs)
    return found


def blueprint_automation_ids(path: str, action_sensor: str) -> list[str] | None:
    """The ids of the automations in the file at path that use the BattMind blueprint for action_sensor.

    Blocking file I/O, for the executor. [] when the file is missing, empty or holds no such automation; None when the
    file exists but can't be read or parsed (then nothing is known). An automation without an id is skipped: the entity
    registry finds an automation by its id only. A numeric id (YAML reads it as a number) is kept as text."""
    if not os.path.isfile(path):
        return []
    try:
        automations = yaml_util.load_yaml(path)
    except (HomeAssistantError, OSError, ValueError) as err:  # ValueError: not UTF-8
        _LOGGER.debug("Could not read %s to see whether the BattMind automation is still there: %s", path, err)
        return None
    if automations is None:
        return []
    if not isinstance(automations, list):
        return None
    ids: list[str] = []
    for automation in automations:
        if _blueprint_input(automation, action_sensor) is None:
            continue
        automation_id = automation.get("id")
        if isinstance(automation_id, bool) or not isinstance(automation_id, (str, int)) or not str(automation_id).strip():
            continue
        ids.append(str(automation_id))
    return ids


def actions_without_steps(inputs: list[dict], actions: tuple[str, ...]) -> set[str]:
    """The actions no automation of inputs has a step for (an empty action does nothing in the blueprint).

    With no automation found nothing is known, so nothing is returned."""
    if not inputs:
        return set()
    return {action for action in actions if not any(automation.get(action) for automation in inputs)}
