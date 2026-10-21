"""Register persistent pages here; keys are independent of navigation order."""

from dataclasses import dataclass
from deepecohab_recorder.tabs.setup import SetupTab
from deepecohab_recorder.tabs.acquisition import AcquisitionTab
from deepecohab_recorder.tabs.animals import AnimalDetailsTab
from deepecohab_recorder.tabs.layout import EcoHABLayoutTab
from deepecohab_recorder.tabs.environment import EnvironmentalSettingsTab
from deepecohab_recorder.tabs.events import EventsTab
from deepecohab_recorder.tabs.camera import CameraControlTab
from deepecohab_recorder.tabs.trigger import HardwareTriggerTab
from deepecohab_recorder.tabs.launch import LaunchTab
from deepecohab_recorder.tabs.data_preprocessing import DataPreprocessingTab


@dataclass(frozen=True)
class PageSpec:
    key: str
    title: str
    factory: object
    scrollable: bool = True
    group: str = "Experiment"
    symbol: str = "notes"
    pinned_bottom: bool = False


PAGES = (
    PageSpec("setup", "Setup", SetupTab, symbol="layout-dashboard"),
    PageSpec("acquisition", "Acquisition", AcquisitionTab, group="Devices", symbol="bolt"),
    PageSpec("animals", "Animal details", AnimalDetailsTab, False, symbol="users"),
    PageSpec("layout", "EcoHAB layout", EcoHABLayoutTab, False, symbol="grid-dots"),
    PageSpec("environment", "Environmental settings", EnvironmentalSettingsTab, symbol="calendar"),
    PageSpec("events", "Events", EventsTab, False),
    PageSpec("camera", "Camera control", CameraControlTab, False, group="Devices", symbol="maximize"),
    PageSpec("trigger", "Hardware trigger", HardwareTriggerTab, group="Devices", symbol="bolt"),
    PageSpec("preprocessing", "Data preprocessing", DataPreprocessingTab, group="Workflow", symbol="table", pinned_bottom=True),
    PageSpec("launch", "Launch", LaunchTab, group="Workflow", symbol="player-play"),
)

# Existing integrations can keep their registry names and numeric page indices.
TabSpec = PageSpec
TABS = PAGES
