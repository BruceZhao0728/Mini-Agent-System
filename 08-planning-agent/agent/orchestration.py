"""Execute a user request directly or supervise its ordered plan steps."""

from config import EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH
from presentation import PlanProgressDisplay

class TaskOrchestrationMixin:
	"""Route requests through planning and coordinate each planned step."""
	def run(self, prompt: str) -> str:
		"""Plan when useful, then execute each planned step through ReAct."""
		previous_display = getattr(self, "_plan_display", None)
		if previous_display:
			previous_display.close()
		self._plan_display = None
		prompt = prompt.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
		first_turn = not self.messages
		if first_turn:
			self.reset()
		if self.task_text is None:
			self.task_text = prompt
		self.turn += 1
		self._rounds_used = 0
		self.plan_request = prompt
		self.plan_error = None
		self.plan = []
		self._refresh_plan_context()

		if EXPORT_LOG and EXPORT_LOG_CHOICES["turn"]:
			with open(EXPORT_LOG_PATH, "a", encoding="utf-8") as f:
				f.write(f"Turn {self.turn}\n--------------------\nUser: {prompt}\n\n")

		needs_plan, steps, error = self._request_plan(prompt)
		if error:
			self.plan_error = error
			print(f"⚠ {error}; falling back to the ReAct loop")
			self._log_section("planning", error)

		if not needs_plan:
			self._refresh_plan_context()
			return self._run_react(prompt, first_turn=first_turn)

		self.plan = [{"text": step, "status": "pending"} for step in steps]
		self._refresh_plan_context()
		self._plan_display = PlanProgressDisplay()
		self._plan_display.update(self.plan)
		if not self._plan_display.active:
			print("\nPlan:")
			for i, step in enumerate(self.plan, 1):
				print(f"  {i}. {step['text']}")
		self._log_section("planning", "Plan:\n" + "\n".join(
			f"{i}. {step['text']}" for i, step in enumerate(self.plan, 1)))

		results = []
		try:
			for i, step in enumerate(self.plan):
				step["status"] = "in_progress"
				self._refresh_plan_context()
				self._plan_display.update(self.plan)
				print(f"\n--- Plan step {i + 1}/{len(self.plan)} ---")
				if i == 0:
					step_prompt = (
						f"Original task: {prompt}\n\n"
						f"Execute only plan step {i + 1}/{len(self.plan)}: {step['text']}\n"
						"Use the available tools as needed. Give a concise step result. "
						"Do not claim the whole task is finished unless this is the final step.")
				else:
					completed = "\n".join(
						f"Step {j + 1} result: {result}" for j, result in enumerate(results))
					step_prompt = (
						f"Original task: {prompt}\n\n"
						f"Already completed work:\n{completed}\n\n"
						f"Execute only plan step {i + 1}/{len(self.plan)}: {step['text']}\n"
						"Use the available tools as needed. Give a concise step result. "
						"Do not claim the whole task is finished unless this is the final step.")
				if i == len(self.plan) - 1:
					step_prompt += (
						" This is the final step: finish by answering the original task, "
						"starting the final response with 'Final Answer:'.")

				try:
					result = self._run_react(step_prompt, first_turn=(first_turn and i == 0))
				except Exception as e:
					step["status"] = "failed"
					self._refresh_plan_context()
					self._plan_display.update(self.plan)
					message = (f"Plan stopped at step {i + 1}/{len(self.plan)}: "
						f"{type(e).__name__}: {e}")
					self._log_section("planning", message)
					return message

				results.append(result)
				if self._execution_failed(result):
					step["status"] = "failed"
					self._refresh_plan_context()
					self._plan_display.update(self.plan)
					message = (f"Plan stopped at step {i + 1}/{len(self.plan)}. "
						f"Execution result: {result}")
					self._log_section("planning", message)
					return message

				step["status"] = "completed"
				self._refresh_plan_context()
				self._plan_display.update(self.plan)
				self._log_section("planning", f"Step {i + 1} completed: {step['text']}")
			return results[-1] if results else "No plan steps were executed."
		finally:
			self._plan_display.close()


