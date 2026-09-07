"""Job execution engine with single-job locking, timeouts, and emergency abort safety."""
try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio


class JobState:
    IDLE = "idle"
    RUNNING = "running"
    ABORTING = "aborting"
    COMPLETED = "completed"
    ERROR = "error"


class JobRunner:
    """Orchestrates job execution, concurrency locking, and safe teardown."""

    def __init__(self, action_registry, context, status_callback=None):
        self.registry = action_registry
        self.context = context
        self.status_callback = status_callback
        self.state = JobState.IDLE
        self.current_job_id = None
        self._current_task = None
        self._abort_requested = False

    @property
    def is_busy(self):
        return self.state == JobState.RUNNING

    async def _publish_status(self, step_idx=0, total_steps=0, extra=None):
        if self.status_callback:
            data = {
                "state": self.state,
                "job_id": self.current_job_id,
                "step": step_idx,
                "total_steps": total_steps
            }
            if extra:
                data.update(extra)
            await self.status_callback(data)

    def emergency_stop(self):
        """Hardware failsafe: immediately de-energize all actuators."""
        if hasattr(self.context, "components") and self.context.components:
            self.context.components.failsafe_all()
        if self.context.solenoid:
            self.context.solenoid.failsafe_reset()
        for pin_name, pin in self.context.pins.items():
            try:
                pin.value(0)
            except Exception:
                pass

    async def abort(self, reason="user_abort"):
        """Emergency abort active job and ensure hardware safety."""
        if not self.is_busy and self.state != JobState.ABORTING:
            return {"status": "ignored", "reason": "Not running"}

        self.state = JobState.ABORTING
        self._abort_requested = True
        self.emergency_stop()

        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

        await self._publish_status(extra={"reason": reason})
        self.state = JobState.IDLE
        self.current_job_id = None
        self._current_task = None
        self._abort_requested = False
        return {"status": "aborted", "reason": reason}

    async def run_job(self, job_name_or_steps, params=None, job_id=None, step_timeout_s=30):
        """
        Run a job consisting of named routine or list of steps.
        Enforces single-job concurrency lock.
        """
        if self.is_busy:
            return {
                "success": False,
                "error": "Device is busy running job: {}".format(self.current_job_id)
            }

        # Resolve steps
        steps = []
        if isinstance(job_name_or_steps, list):
            steps = job_name_or_steps
            job_name = "adhoc"
        elif isinstance(job_name_or_steps, str):
            job_name = job_name_or_steps
            configured_routines = self.context.config.routines if self.context.config else {}
            if job_name in configured_routines:
                # Copy routine template
                import json
                steps = json.loads(json.dumps(configured_routines[job_name]))
            elif job_name == "enroll_step":
                steps = [{"action": "as608_enroll_step", "slot_id": (params or {}).get("slot_id")}]
            elif job_name == "delete_slot":
                steps = [{"action": "as608_delete", "slot_id": (params or {}).get("slot_id")}]
            else:
                return {"success": False, "error": "Unknown routine: {}".format(job_name)}
        else:
            return {"success": False, "error": "Invalid job specification"}

        # Apply parameter overrides to steps if provided
        if params and isinstance(params, dict):
            for step in steps:
                for k, v in params.items():
                    if k in step or step.get("action") == "pulse" and k == "duration_ms":
                        step[k] = v

        self.state = JobState.RUNNING
        self.current_job_id = job_id or job_name
        self._abort_requested = False
        self._current_task = asyncio.current_task()

        total_steps = len(steps)
        await self._publish_status(step_idx=0, total_steps=total_steps)

        results = []
        try:
            for idx, step in enumerate(steps, start=1):
                if self._abort_requested:
                    raise asyncio.CancelledError()

                await self._publish_status(step_idx=idx, total_steps=total_steps, extra={"current_action": step.get("action")})

                # Execute action with timeout
                step_task = self.registry.execute(step, self.context)
                if hasattr(asyncio, "wait_for"):
                    step_res = await asyncio.wait_for(step_task, step_timeout_s)
                else:
                    # MicroPython uasyncio wait_for_ms
                    step_res = await asyncio.wait_for_ms(step_task, step_timeout_s * 1000)
                results.append(step_res)

            self.state = JobState.COMPLETED
            await self._publish_status(step_idx=total_steps, total_steps=total_steps, extra={"results": results})
            return {"success": True, "job_id": self.current_job_id, "results": results}

        except asyncio.CancelledError:
            self.emergency_stop()
            self.state = JobState.IDLE
            await self._publish_status(extra={"reason": "cancelled"})
            return {"success": False, "error": "Job cancelled"}

        except Exception as ex:
            self.emergency_stop()
            self.state = JobState.ERROR
            err_msg = str(ex)
            await self._publish_status(extra={"error": err_msg})
            return {"success": False, "error": err_msg}

        finally:
            self.emergency_stop()
            self.state = JobState.IDLE
            self.current_job_id = None
            self._current_task = None
