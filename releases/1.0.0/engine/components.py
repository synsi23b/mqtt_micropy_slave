"""Dynamic Hardware Component Manager.
Instantiates and coordinates only the components explicitly declared in config.json.
"""
from drivers.solenoid import SolenoidLock
from drivers.servo import Servo
from drivers.digital_io import DigitalInput, DigitalOutput
from drivers.analog_io import AnalogInput
from drivers.as608 import AS608


class ComponentManager:
    """Registry managing active peripheral drivers based on configuration."""

    def __init__(self, components_cfg=None):
        self._components = {}
        self._types = {}
        if components_cfg:
            self.load_components(components_cfg)

    def load_components(self, components_cfg):
        """Parse dictionary of component declarations and instantiate drivers."""
        for comp_id, spec in components_cfg.items():
            comp_type = spec.get("type")
            driver = self._instantiate_driver(comp_id, comp_type, spec)
            if driver is not None:
                self._components[comp_id] = driver
                if comp_type not in self._types:
                    self._types[comp_type] = []
                self._types[comp_type].append(comp_id)

    def _instantiate_driver(self, comp_id, comp_type, spec):
        if comp_type == "solenoid":
            return SolenoidLock(
                pin_num=spec.get("pin", 23),
                active_high=spec.get("active_high", True),
                default_pulse_ms=spec.get("default_pulse_ms", 3000),
                max_pulse_ms=spec.get("max_pulse_ms", 10000)
            )

        elif comp_type == "servo":
            return Servo(
                pin_num=spec.get("pin", 25),
                min_us=spec.get("min_us", 500),
                max_us=spec.get("max_us", 2500),
                max_angle=spec.get("max_angle", 180)
            )

        elif comp_type == "digital_out":
            return DigitalOutput(
                pin_num=spec.get("pin", 2),
                active_high=spec.get("active_high", True),
                initial_state=spec.get("initial_state", 0)
            )

        elif comp_type == "digital_in":
            return DigitalInput(
                pin_num=spec.get("pin", 4),
                pull=spec.get("pull", "none"),
                invert=spec.get("invert", False),
                debounce_ms=spec.get("debounce_ms", 50),
                report_changes=spec.get("report_changes", False),
                ha_device_class=spec.get("ha_device_class")
            )

        elif comp_type == "analog_in":
            return AnalogInput(
                pin_num=spec.get("pin", 36),
                report_interval_s=spec.get("report_interval_s", 60),
                ha_device_class=spec.get("ha_device_class", "voltage")
            )

        elif comp_type == "as608":
            try:
                from machine import UART
                uart = UART(
                    spec.get("uart_id", 2),
                    baudrate=spec.get("baudrate", 57600),
                    tx=spec.get("tx_pin", 17),
                    rx=spec.get("rx_pin", 16)
                )
                return AS608(
                    uart,
                    address=spec.get("address", 0xFFFFFFFF),
                    password=spec.get("password", 0)
                )
            except Exception as e:
                print("[ComponentManager] Warning: failed to init AS608 UART:", e)
                return None

        else:
            print("[ComponentManager] Unknown component type:", comp_type)
            return None

    def get(self, comp_id):
        """Retrieve component instance by its configured ID."""
        return self._components.get(comp_id)

    def get_by_type(self, comp_type):
        """Return list of (comp_id, instance) for all components of a given type."""
        ids = self._types.get(comp_type, [])
        return [(cid, self._components[cid]) for cid in ids if cid in self._components]

    def get_reporting_inputs(self):
        """Return list of (comp_id, DigitalInput) that have report_changes enabled."""
        res = []
        for cid, comp in self.get_by_type("digital_in"):
            if comp.report_changes:
                res.append((cid, comp))
        return res

    def get_reporting_analogs(self):
        """Return list of (comp_id, AnalogInput)."""
        return self.get_by_type("analog_in")

    def failsafe_all(self):
        """Emergency reset: force all outputs and solenoids to safe/idle state."""
        for _, comp in self.get_by_type("solenoid"):
            comp.failsafe_reset()
        for _, comp in self.get_by_type("digital_out"):
            comp.write(0)

    @property
    def all_components(self):
        return self._components
