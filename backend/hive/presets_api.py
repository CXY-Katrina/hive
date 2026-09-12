"""Thin preset routes; imported catalog contents never execute here."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class PresetSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pr: int | str
    head_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    vllm_sha: str = Field(pattern=r'^[0-9a-f]{40}$')


class ImportPresets(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: PresetSource
    path: str = Field(max_length=512)


def register_preset_routes(app, services, current, respond):
    @app.get('/api/presets')
    def presets(actor=Depends(current)):
        return respond(services.presets.list())

    @app.post('/api/presets/import')
    def import_presets(body: ImportPresets, actor=Depends(current)):
        return respond(services.presets.import_catalog(actor, body.source.model_dump(), body.path),201)

    @app.post('/api/presets/{preset_id}/enable')
    def enable_preset(preset_id: str, actor=Depends(current)):
        return respond(services.presets.enable(actor,preset_id))
