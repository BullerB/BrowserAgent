"""Result schemas.

A goal declares what a "row" of its answer looks like. The LLM extractor is
handed the field list; the heuristic fallback only understands prices.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResultField(BaseModel):
    name: str
    description: str
    required: bool = False


class ResultSchema(BaseModel):
    """Describes the records a goal should produce."""

    name: str
    description: str
    fields: list[ResultField] = Field(default_factory=list)

    def to_prompt(self) -> str:
        lines = [f"SCHEMA {self.name}: {self.description}", "FIELDS:"]
        lines += [
            f"- {f.name}{' (required)' if f.required else ''}: {f.description}"
            for f in self.fields
        ]
        return "\n".join(lines)


QUOTE_SCHEMA = ResultSchema(
    name="insurance_quote",
    description="One insurance offer shown on a comparison results page.",
    fields=[
        ResultField(name="company", description="Insurer name", required=True),
        ResultField(name="price", description="Numeric price, digits only", required=True),
        ResultField(name="currency", description="Currency code, e.g. DKK"),
        ResultField(name="period", description="What the price covers: year, month or quarter"),
        ResultField(name="deductible", description="Excess / selvrisiko, numeric"),
        ResultField(name="coverage", description="Coverage level or package name"),
        ResultField(name="notes", description="Anything else that qualifies the offer"),
    ],
)

#: Schemas addressable by name from a recorded ``extract`` step.
BUILTIN_SCHEMAS: dict[str, ResultSchema] = {QUOTE_SCHEMA.name: QUOTE_SCHEMA}
