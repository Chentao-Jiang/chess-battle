"""Agent memory for cross-turn strategic continuity."""
from dataclasses import dataclass, field


@dataclass
class AgentMemory:
    """Persistent memory for one side, accumulated across turns.
    Updated from the model's own JSON response — no extra API calls needed.
    """

    strategic_plan: str = ""
    positional_assessment: str = ""
    opponent_style: str = ""
    observed_motifs: list[str] = field(default_factory=list)
    plan_stage: str = "opening"
    recent_insights: list[str] = field(default_factory=list)

    def update_from_response(self, parsed: dict):
        """Update memory from the model's parsed move response."""
        if not parsed:
            return
        if parsed.get("strategic_plan"):
            self.strategic_plan = parsed["strategic_plan"]
        if parsed.get("positional_assessment"):
            self.positional_assessment = parsed["positional_assessment"]
        if parsed.get("opponent_observation"):
            self.opponent_style = parsed["opponent_observation"]
        if parsed.get("strategic_reflection"):
            self.recent_insights.append(parsed["strategic_reflection"])
            if len(self.recent_insights) > 3:
                self.recent_insights = self.recent_insights[-3:]

    def add_motif(self, motif: str):
        self.observed_motifs.append(motif)
        if len(self.observed_motifs) > 10:
            self.observed_motifs = self.observed_motifs[-10:]

    def to_context(self) -> str:
        """Format as compact text block for prompt injection. Max ~500 chars."""
        parts = []
        if self.strategic_plan:
            parts.append(f"战略：{self.strategic_plan}")
        if self.positional_assessment:
            parts.append(f"形势：{self.positional_assessment}")
        if self.opponent_style:
            parts.append(f"对手：{self.opponent_style}")
        if self.observed_motifs:
            parts.append(f"已用战术：{'、'.join(self.observed_motifs[-5:])}")
        if self.recent_insights:
            parts.append(f"近期判断：{'；'.join(self.recent_insights)}")
        if not parts:
            return ""
        return "【记忆】\n" + "\n".join(parts)

    def reset(self):
        self.strategic_plan = ""
        self.positional_assessment = ""
        self.opponent_style = ""
        self.observed_motifs = []
        self.plan_stage = "opening"
        self.recent_insights = []

    def to_dict(self) -> dict:
        return {
            "strategic_plan": self.strategic_plan,
            "positional_assessment": self.positional_assessment,
            "opponent_style": self.opponent_style,
            "observed_motifs": self.observed_motifs,
            "plan_stage": self.plan_stage,
            "recent_insights": self.recent_insights,
        }
