from pydantic import BaseModel


class MOAModel(BaseModel):
    """Base model for every MOA data object."""
    pass


class NamedEntity(MOAModel):
    name: str