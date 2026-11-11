from pydantic import BaseModel, Field, field_validator


class SettingsPayload(BaseModel):
    ollama_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=255)
    embedding_model: str = Field(default="", max_length=255)
    ocr_language: str = Field(default="deu+eng", min_length=2, max_length=50)
    chunk_size: int = Field(default=500, ge=100, le=5000)
    cards_per_chunk: int = Field(default=5, ge=1, le=20)
    output_format: str = Field(default="anki_csv", pattern="^(anki_csv|json|markdown)$")
    temperature: float = Field(default=0.3, ge=0, le=1)
    context_length: int = Field(default=4096, ge=512, le=131072)

    @field_validator("ollama_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("Die Ollama-URL muss mit http:// oder https:// beginnen.")
        return value


class CardUpdate(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    answer: str = Field(min_length=1, max_length=20000)


class CardOut(BaseModel):
    id: int
    question: str
    answer: str

    model_config = {"from_attributes": True}