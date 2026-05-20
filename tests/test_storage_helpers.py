import importlib.util
import sys
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONST_PATH = REPO_ROOT / "custom_components" / "chores4kids" / "const.py"
STORAGE_PATH = REPO_ROOT / "custom_components" / "chores4kids" / "storage.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_storage_module():
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    helpers_storage = types.ModuleType("homeassistant.helpers.storage")
    util = types.ModuleType("homeassistant.util")
    util_dt = types.ModuleType("homeassistant.util.dt")

    class HomeAssistant:
        pass

    class Store:
        def __init__(self, *_args, **_kwargs):
            pass

    def parse_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    core.HomeAssistant = HomeAssistant
    helpers_storage.Store = Store
    util_dt.now = lambda: datetime.now(timezone.utc)
    util_dt.parse_datetime = parse_datetime

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = helpers_storage
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = util_dt

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(REPO_ROOT / "custom_components")]
    chores_pkg = types.ModuleType("custom_components.chores4kids")
    chores_pkg.__path__ = [str(REPO_ROOT / "custom_components" / "chores4kids")]

    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.chores4kids"] = chores_pkg

    _load_module("custom_components.chores4kids.const", CONST_PATH)
    return _load_module("custom_components.chores4kids.storage", STORAGE_PATH)


storage = load_storage_module()


def make_store():
    instance = object.__new__(storage.KidsChoresStore)
    instance.tasks = []
    instance.categories = []
    instance.items = []
    return instance


class TestStorageHelpers(unittest.TestCase):
    def test_slugify_normalizes_and_falls_back(self):
        self.assertEqual(storage.slugify("Émma !!!"), "emma")
        self.assertEqual(storage.slugify("   "), "child")

    def test_next_repeat_due_iso_handles_today_and_next_match(self):
        store = make_store()
        base = date(2026, 5, 20)  # Wednesday

        self.assertEqual(store._next_repeat_due_iso(base, [2, 4, 2]), "2026-05-20")
        self.assertEqual(store._next_repeat_due_iso(base, [2], include_today=False), "2026-05-27")
        self.assertIsNone(store._next_repeat_due_iso(base, []))

    def test_next_monthly_due_iso_handles_same_day_and_year_rollover(self):
        store = make_store()

        self.assertEqual(store._next_monthly_due_iso(date(2026, 5, 1), include_today=True), "2026-05-01")
        self.assertEqual(store._next_monthly_due_iso(date(2026, 5, 1), include_today=False), "2026-06-01")
        self.assertEqual(store._next_monthly_due_iso(date(2026, 12, 21)), "2027-01-01")
        self.assertIsNone(store._next_monthly_due_iso("2026-12-21"))

    def test_repeat_targets_deduplicates_and_keeps_order(self):
        store = make_store()
        task = storage.Task(
            id="t1",
            title="Template",
            points=5,
            repeat_child_ids=["c1", "c2", "c1"],
            repeat_child_id="c3",
        )

        self.assertEqual(store._repeat_targets_for_template(task), ["c1", "c2", "c3"])

    def test_resolve_task_id_prefers_direct_assignment_for_child(self):
        store = make_store()
        assigned = storage.Task(id="task-1", title="Laundry", points=3, assigned_to="child-1")
        store.tasks = [assigned]

        self.assertEqual(store.resolve_task_id_for_child("task-1", "child-1"), "task-1")

    def test_resolve_task_id_finds_assigned_instance_from_template(self):
        store = make_store()
        template = storage.Task(id="template-1", title="Dishes", points=2)
        approved = storage.Task(
            id="assigned-approved",
            title="Dishes",
            points=2,
            assigned_to="child-1",
            repeat_template_id="template-1",
            status=storage.STATUS_APPROVED,
            created="2026-05-21T00:00:00+00:00",
        )
        assigned = storage.Task(
            id="assigned-active",
            title="Dishes",
            points=2,
            assigned_to="child-1",
            repeat_template_id="template-1",
            status=storage.STATUS_ASSIGNED,
            created="2026-05-19T00:00:00+00:00",
        )
        store.tasks = [template, approved, assigned]

        self.assertEqual(store.resolve_task_id_for_child("template-1", "child-1"), "assigned-active")

    def test_resolve_task_id_returns_base_when_no_candidate(self):
        store = make_store()
        template = storage.Task(id="template-2", title="Vacuum", points=4)
        store.tasks = [template]

        self.assertEqual(store.resolve_task_id_for_child("template-2", "child-9"), "template-2")

    def test_normalize_hex_color_accepts_short_and_long_hex(self):
        store = make_store()

        self.assertEqual(store._normalize_hex_color("abc"), "#aabbcc")
        self.assertEqual(store._normalize_hex_color("#A1B2C3"), "#a1b2c3")
        self.assertEqual(store._normalize_hex_color(""), "")

    def test_normalize_hex_color_rejects_invalid_values(self):
        store = make_store()

        with self.assertRaisesRegex(ValueError, "invalid_color"):
            store._normalize_hex_color("not-a-color")

    def test_normalize_actions_maps_and_filters_steps(self):
        store = make_store()

        actions = store._normalize_actions(
            [
                {"type": "delay", "seconds": 10},
                {"type": "delay", "seconds": 0},
                {"type": "entity_service", "entity_id": "switch.tv", "op": "turn_off"},
                {"type": "service", "entity_id": "light.kitchen", "service": "turn_on", "data": {"brightness": 120}},
                {"type": "service", "entity_id": "", "service": "turn_on"},
                {"type": "unknown"},
            ]
        )

        self.assertEqual(
            actions,
            [
                {"type": "delay", "seconds": 10},
                {
                    "type": "service",
                    "domain": "switch",
                    "service": "turn_off",
                    "entity_id": "switch.tv",
                    "data": {},
                },
                {
                    "type": "service",
                    "domain": "light",
                    "service": "turn_on",
                    "entity_id": "light.kitchen",
                    "data": {"brightness": 120},
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
