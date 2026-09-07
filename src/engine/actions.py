"""Modular Action Registry for physical job routines.
Targets dynamically declared components from ComponentManager or raw GPIO pins.
"""
try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    # pyrefly: ignore [missing-import]
    from machine import Pin
except ImportError:
    class Pin:
        OUT = 1
        IN = 0
        def __init__(self, pin, mode=1, value=0):
            self._pin = pin
            self._val = value
        def value(self, val=None):
            if val is not None:
                self._val = val
            return self._val


class ActionExecutionContext:
    """Carries hardware components and publish callbacks into action handlers."""

    def __init__(self, config=None, components=None, pins=None, solenoid=None, as608=None, mqtt_publish_cb=None, event_cb=None):
        self.config = config
        self.components = components  # ComponentManager
        self.pins = pins or {}
        self._legacy_solenoid = solenoid
        self._legacy_as608 = as608
        self.mqtt_publish_cb = mqtt_publish_cb
        self.event_cb = event_cb

    @property
    def solenoid(self):
        if self.components:
            sols = self.components.get_by_type("solenoid")
            if sols:
                return sols[0][1]
        return self._legacy_solenoid

    @property
    def as608(self):
        if self.components:
            sensors = self.components.get_by_type("as608")
            if sensors:
                return sensors[0][1]
        return self._legacy_as608

    def resolve_target(self, target_id):
        """Find component by ID from ComponentManager, or fallback to pins."""
        if self.components:
            comp = self.components.get(target_id)
            if comp is not None:
                return comp

        if target_id == "solenoid" and self.solenoid:
            return self.solenoid
        if target_id == "as608" and self.as608:
            return self.as608

        # Check in legacy pins dict
        if target_id in self.pins:
            return self.pins[target_id]

        # Raw pin integer
        if isinstance(target_id, int):
            key = "raw_pin_{}".format(target_id)
            if key not in self.pins:
                self.pins[key] = Pin(target_id, Pin.OUT)
            return self.pins[key]

        return None


class ActionRegistry:
    """Registry mapping action names to async execution handlers."""

    def __init__(self):
        self._actions = {}
        self._register_builtins()

    def register(self, name, handler):
        self._actions[name] = handler

    def get(self, name):
        return self._actions.get(name)

    def _register_builtins(self):
        self.register("digital_write", self._act_digital_write)
        self.register("pulse", self._act_pulse)
        self.register("read", self._act_read)
        self.register("delay", self._act_delay)
        self.register("pwm_write", self._act_pwm_write)
        self.register("servo_set", self._act_servo_set)
        self.register("serial_io", self._act_serial_io)
        self.register("as608_search", self._act_as608_search)
        self.register("as608_enroll_step", self._act_as608_enroll_step)
        self.register("as608_wait_finger_lift", self._act_as608_wait_lift)
        self.register("as608_delete", self._act_as608_delete)
        self.register("mqtt_publish", self._act_mqtt_publish)

    async def execute(self, step, context):
        """Execute a single action dictionary using the registry."""
        action_name = step.get("action")
        if not action_name:
            raise ValueError("Action step missing 'action' field")
        handler = self.get(action_name)
        if not handler:
            raise ValueError("Unknown action: {}".format(action_name))
        return await handler(step, context)

    # Handlers
    async def _act_digital_write(self, step, ctx):
        target_id = step.get("target", step.get("pin"))
        state = 1 if step.get("state") in (1, True, "high", "HIGH") else 0
        obj = ctx.resolve_target(target_id)

        if hasattr(obj, "write"):  # DigitalOutput
            obj.write(state)
        elif hasattr(obj, "value"):  # Pin
            obj.value(state)
        elif hasattr(obj, "lock") and hasattr(obj, "unlock"):  # SolenoidLock
            if state:
                asyncio.create_task(obj.unlock())
            else:
                obj.lock()
        else:
            raise ValueError("Target '{}' cannot perform digital_write".format(target_id))

        return {"action": "digital_write", "target": target_id, "state": state}

    async def _act_pulse(self, step, ctx):
        target_id = step.get("target", step.get("pin"))
        duration_ms = step.get("duration_ms", 100)
        obj = ctx.resolve_target(target_id)

        if hasattr(obj, "unlock") and hasattr(obj, "failsafe_reset"):  # SolenoidLock
            await obj.unlock(duration_ms=duration_ms)
        elif hasattr(obj, "pulse"):  # DigitalOutput
            await obj.pulse(duration_ms=duration_ms)
        elif hasattr(obj, "value"):  # Pin
            active_high = step.get("active_high", True)
            active_val = 1 if active_high else 0
            idle_val = 0 if active_high else 1
            obj.value(active_val)
            try:
                await asyncio.sleep_ms(duration_ms)
            finally:
                obj.value(idle_val)
        else:
            raise ValueError("Target '{}' cannot perform pulse".format(target_id))

        return {"action": "pulse", "target": target_id, "duration_ms": duration_ms}

    async def _act_read(self, step, ctx):
        target_id = step.get("target", step.get("pin"))
        obj = ctx.resolve_target(target_id)
        val = None

        if hasattr(obj, "read_voltage"):  # AnalogInput
            val = obj.read_voltage()
        elif hasattr(obj, "read"):  # DigitalInput
            val = obj.read()
        elif hasattr(obj, "value"):  # Pin
            val = obj.value()
        else:
            raise ValueError("Target '{}' cannot perform read".format(target_id))

        res = {"action": "read", "target": target_id, "value": val}
        if ctx.event_cb:
            await ctx.event_cb("input_read", res)
        return res

    async def _act_delay(self, step, ctx):
        duration_ms = step.get("duration_ms", step.get("ms", 100))
        await asyncio.sleep_ms(duration_ms)
        return {"action": "delay", "duration_ms": duration_ms}

    async def _act_pwm_write(self, step, ctx):
        target_id = step.get("target", step.get("pin"))
        freq = step.get("freq", 1000)
        duty_u16 = step.get("duty_u16", 32768)
        # Raw pin PWM
        obj = ctx.resolve_target(target_id)
        if hasattr(obj, "pin"):
            raw_pin = obj.pin
        else:
            raw_pin = obj
        try:
            # pyrefly: ignore [missing-import]
            from machine import PWM
            pwm = PWM(raw_pin, freq=freq, duty_u16=duty_u16)
        except Exception:
            pass
        return {"action": "pwm_write", "target": target_id, "freq": freq, "duty_u16": duty_u16}

    async def _act_servo_set(self, step, ctx):
        target_id = step.get("target", step.get("pin"))
        angle = step.get("angle")
        pulse_us = step.get("pulse_us")
        obj = ctx.resolve_target(target_id)

        # If already a Servo instance
        if hasattr(obj, "set_angle"):
            servo = obj
        else:
            from drivers.servo import Servo
            pin_num = target_id if isinstance(target_id, int) else 25
            servo = Servo(pin_num)

        if angle is not None:
            servo.set_angle(angle)
        elif pulse_us is not None:
            servo.set_pulse_us(pulse_us)

        return {"action": "servo_set", "target": target_id, "angle": angle, "pulse_us": pulse_us}

    async def _act_serial_io(self, step, ctx):
        data = step.get("write")
        sensor = ctx.as608
        if data and sensor and hasattr(sensor, "uart"):
            if isinstance(data, str):
                data = data.encode("utf-8")
            sensor.uart.write(data)
        return {"action": "serial_io", "bytes_written": len(data) if data else 0}

    async def _act_as608_search(self, step, ctx):
        sensor = ctx.as608
        if not sensor:
            raise RuntimeError("AS608 sensor not configured")
        found, page_id, score, err = await sensor.search_once()
        res = {"found": found, "finger_id": page_id, "confidence": score, "err": err}
        if ctx.event_cb:
            await ctx.event_cb("fingerprint_scanned", res)
        return res

    async def _act_as608_enroll_step(self, step, ctx):
        sensor = ctx.as608
        if not sensor:
            raise RuntimeError("AS608 sensor not configured")
        slot_id = step.get("slot_id")
        if slot_id is None:
            raise ValueError("as608_enroll_step requires 'slot_id'")
        timeout_s = step.get("timeout_s", 15)

        if ctx.event_cb:
            await ctx.event_cb("enroll_state", {"state": "waiting_for_finger", "slot_id": slot_id})

        ok, state, err = await sensor.enroll_single_sample(slot_id, timeout_s=timeout_s)
        res = {"ok": ok, "state": state, "slot_id": slot_id, "error": err}
        if ctx.event_cb:
            await ctx.event_cb("enroll_state", res)
        return res

    async def _act_as608_wait_lift(self, step, ctx):
        sensor = ctx.as608
        if not sensor:
            raise RuntimeError("AS608 sensor not configured")
        timeout_s = step.get("timeout_s", 5)
        lifted = await sensor.wait_finger_lift(timeout_s=timeout_s)
        return {"action": "as608_wait_finger_lift", "lifted": lifted}

    async def _act_as608_delete(self, step, ctx):
        sensor = ctx.as608
        if not sensor:
            raise RuntimeError("AS608 sensor not configured")
        slot_id = step.get("slot_id")
        if slot_id is None:
            raise ValueError("as608_delete requires 'slot_id'")
        count = step.get("count", 1)
        ok, err = await sensor.delete_slot(slot_id, count=count)
        return {"action": "as608_delete", "slot_id": slot_id, "ok": ok, "error": err}

    async def _act_mqtt_publish(self, step, ctx):
        topic = step.get("topic")
        payload = step.get("payload", "")
        if ctx.mqtt_publish_cb and topic:
            await ctx.mqtt_publish_cb(topic, payload)
        return {"action": "mqtt_publish", "topic": topic}
