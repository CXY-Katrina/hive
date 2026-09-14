"""Public preset administration; permissions are enforced in the service."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class PublicPreset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1,max_length=128)
    tags: dict[str,str] = Field(default_factory=dict,max_length=32)
    remarks: str = Field(default='',max_length=4000)
    yaml_path: str = Field(default='',max_length=512)
    base_preset_id: str | None = Field(default=None,max_length=64)
    workflow: dict


class PresetRemarks(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    remarks: str = Field(max_length=4000)


def register_management_routes(app,services,current,respond):
    @app.post('/api/presets/public')
    def create_public_preset(body: PublicPreset, actor=Depends(current)):
        return respond(services.presets.management.create(actor,**body.model_dump()),201)

    @app.patch('/api/presets/{preset_id}/remarks')
    def update_preset_remarks(preset_id: str,body: PresetRemarks,actor=Depends(current)):
        return respond(services.presets.management.remarks(actor,preset_id,body.remarks))
