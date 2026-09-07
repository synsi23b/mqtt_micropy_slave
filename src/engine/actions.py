"""Modular Action Registry for physical job routines."""
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from machine import Pin, PWM
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
    """Carries hardware instances and publish callbacks into action handlers."""

    def __init__(self, config=None, pins=None, solenoid=None, as608=None, mqtt_publish_cb=None, event_cb=None):
        self.config = config
        self.pins = pins or {}
        self.solenoid = solenoid
        self.as608 = as608
        self.mqtt_publish_cb = mqtt_publish_cb
        self.event_cb = event_cb
        self.servos = {}

    def get_or_create_pin(self, pin_spec):
        """Resolve pin name (e.g. 'solenoid', 'buzzer') or raw integer to a Pin object."""
        if isinstance(pin_spec, str) and pin_spec in self.pins:
            return self.pins[pin_spec]
        if isinstance(pin_spec, str) and self.config:
            cfg_pin = self.config.get("hardware", pin_spec, "pin")
            if cfg_pin is not None:
                if pin_spec not in self.pins:
                    self.pins[pin_spec] = Pin(cfg_pin, Pin.OUT)
                return self.pins[pin_spec]
        if isinstance(pin_spec, int):
            key = "raw_pin_{}".format(pin_spec)
            if key not in self.pins:
                self.pins[key] = Pin(pin_spec, Pin.OUT)
            return self.pins[key]
        raise ValueError("Unknown pin identifier: {}".format(pin_spec))


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

    # Built-in Handlers
    async def _act_digital_write(self, step, ctx):
        pin_id = step.get("pin")
        state = 1 if step.get("state") in (1, True, "high", "HIGH") else 0
        pin = ctx.get_or_create_pin(pin_id)
        pin.value(state)
        return {"action": "digital_write", "pin": pin_id, "state": state}

    async def _act_pulse(self, step, ctx):
        pin_id = step.get("pin")
        duration_ms = step.get("duration_ms", 100)

        # If targeting solenoid specifically, use solenoid driver for safety tracking
        if pin_id == "solenoid" and ctx.solenoid:
            await ctx.solenoid.unlock(duration_ms=duration_ms)
            return {"action": "pulse", "pin": "solenoid", "duration_ms": duration_ms}

        pin = ctx.get_or_create_pin(pin_id)
        active_high = step.get("active_high", True)
        active_val = 1 if active_high else 0
        idle_val = 0 if active_high else 1

        pin.value(active_val)
        try:
            await asyncio.sleep_ms(duration_ms)
        finally:
            pin.value(idle_val)
        return {"action": "pulse", "pin": pin_id, "duration_ms": duration_ms}

    async def _act_delay(self, step, ctx):
        duration_ms = step.get("duration_ms", step.get("ms", 100))
        await asyncio.sleep_ms(duration_ms)
        return {"action": "delay", "duration_ms": duration_ms}

    async def _act_pwm_write(self, step, ctx):
        pin_id = step.get("pin")
        freq = step.get("freq", 1000)
        duty_u16 = step.get("duty_u16", 32768)
        raw_pin = ctx.get_or_create_pin(pin_id)
        pwm_key = "pwm_{}".format(pin_id)
        if pwm_key not in ctx.servos:
            try:
                from machine import PWM
                ctx.servos[pwm_key] = PWM(raw_pin, freq=freq, duty_u16=duty_u16)
            except Exception:
                pass
        else:
            pwm = ctx.servos[pwm_key]
            if hasattr(pwm, "freq"):
                pwm.freq(freq)
            if hasattr(pwm, "duty_u16"):
                pwm.duty_u16(duty_u16)
        return {"action": "pwm_write", "pin": pin_id, "freq": freq, "duty_u16": duty_u16}

    async def _act_servo_set(self, step, ctx):
        from drivers.servo import Servo
        pin_id = step.get("pin")
        angle = step.get("angle")
        pulse_us = step.get("pulse_us")

        servo_key = "servo_{}".format(pin_id)
        if servo_key not in ctx.servos:
            pin_num = pin_id if isinstance(pin_id, int) else 25
            ctx.servos[servo_key] = Servo(pin_num)
        servo = ctx.servos[servo_key]

        if angle is not None:
            servo.set_angle(angle)
        elif pulse_us is not None:
            servo.set_pulse_us(pulse_us)
        return {"action": "servo_set", "pin": pin_id, "angle": angle, "pulse_us": pulse_us}

    async def _act_serial_io(self, step, ctx):
        data = step.get("write")
        if data and ctx.as608 and hasattr(ctx.as608, "uart"):
            if isinstance(data, str):
                data = data.encode("utf-8")
            ctx.as608.uart.write(data)
        return {"action": "serial_io", "bytes_written": len(data) if data else 0}

    async def _act_as608_search(self, step, ctx):
        if not ctx.as608:
            raise RuntimeError("AS608 sensor not configured")
        found, page_id, score, err = await ctx.as608.search_once()
        res = {"found": found, "finger_id": page_id, "confidence": score, "err": err}
        if ctx.event_cb:
            await ctx.event_cb("fingerprint_scanned", res)
        return res

    async def _act_as608_enroll_step(self, step, ctx):
        if not ctx.as608:
            raise RuntimeError("AS608 sensor not configured")
        slot_id = step.get("slot_id")
        if slot_id is None:
            raise ValueError("as608_enroll_step requires 'slot_id'")
        timeout_s = step.get("timeout_s", 15)

        if ctx.event_cb:
            await ctx.event_cb("enroll_state", {"state": "waiting_for_finger", "slot_id": slot_id})

        ok, state, err = await ctx.as608.enroll_single_sample(slot_id, timeout_s=timeout_s)
        res = {"ok": ok, "state": state, "slot_id": slot_id, "error": err}
        if ctx.event_cb:
            await ctx.event_cb("enroll_state", res)
        return res

    async def _act_as608_wait_lift(self, step, ctx):
        if not ctx.as608:
            raise RuntimeError("AS608 sensor not configured")
        timeout_s = step.get("timeout_s", 5)
        lifted = await ctx.as608.wait_finger_lift(timeout_s=timeout_s)
        return {"action": "as608_wait_finger_lift", "lifted": lifted}

    async def _act_as608_delete(self, step, ctx):
        if not ctx.as608:
            raise RuntimeError("AS608 sensor not configured")
        slot_id = step.get("slot_id")
        if slot_id is None:
            raise ValueError("as608_delete requires 'slot_id'")
        count = step.get("count", 1)
        ok, err = await ctx.as608.delete_slot(slot_id, count=count)
        return {"action": "as608_delete", "slot_id": slot_id, "ok": ok, "error": err}

    async def _act_mqtt_publish(self, step, ctx):
        topic = step.get("topic")
        payload = step.get("payload", "")
        if ctx.mqtt_publish_cb and topic:
            await ctx.mqtt_publish_cb(topic, payload)
        return {"action": "mqtt_publish", "topic": topic}
