"""Thin preset routes; imported catalog contents never execute here."""
from fastapi import Depends
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class PresetSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pr: int | str
    revision: Literal['head', 'merged'] = 'head'
    head_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    vllm_sha: str = Field(pattern=r'^[0-9a-f]{40}$')


class ImportPresets(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: PresetSource
    path: str = Field(max_length=512)


class DerivePreset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=128)
    tags: dict[str, str] = Field(default_factory=dict)
    workflow: dict


class PublishWorkflow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    workflow_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    item_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
    name: str = Field(min_length=1, max_length=128)
    tags: dict[str, str] = Field(default_factory=dict)


def register_preset_routes(app, services, current, respond):
    from .preset_management_api import register_management_routes
    register_management_routes(app, services, current, respond)

    @app.get('/api/presets')
    def presets(actor=Depends(current)):
        return respond(services.presets.list(actor))

    @app.post('/api/presets/from-workflow')
    def publish_workflow(body: PublishWorkflow, actor=Depends(current)):
        return respond(services.presets.from_workflow(actor, **body.model_dump()), 201)

    @app.post('/api/presets/import')
    def import_presets(body: ImportPresets, actor=Depends(current)):
        return respond(services.presets.import_catalog(actor, body.source.model_dump(), body.path),201)

    @app.post('/api/presets/{preset_id}/enable')
    def enable_preset(preset_id: str, actor=Depends(current)):
        return respond(services.presets.enable(actor,preset_id))

    @app.post('/api/presets/{preset_id}/derive')
    def derive_preset(preset_id: str, body: DerivePreset, actor=Depends(current)):
        return respond(services.presets.derive(actor,preset_id,body.name,body.tags,body.workflow),201)
