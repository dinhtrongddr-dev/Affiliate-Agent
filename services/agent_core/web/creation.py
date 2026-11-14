"""Explicit creation branches and reusable cast management for the local UI."""
import json

from fastapi import File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import catalog, creative_series, db, workflow
from ..creation_context import build_product_context


class Creation(BaseModel):
    creation_branch: str
    topic: str = Field(default='', max_length=1000)
    catalog_id: str = Field(default='', max_length=100)
    experience: dict = Field(default_factory=dict)
    template_id: str = Field(default='', max_length=64)
    fast_mode: bool = False


def product_offer(settings, product):
    context = build_product_context(settings, product['item_id'])
    offer = catalog.as_offer(product)
    offer['imageUrl'] = next((r['url'] for r in context['image_references'] if r['approvedForRender']), '')
    return offer, context


def install_routes(app, settings, render):
    @app.get('/create')
    def workspace(request: Request, branch: str = 'product_review', catalog_id: str = ''):
        s = settings()
        products = catalog.list_products(s.database_path, limit=300)
        if catalog_id and not any(p['id'] == catalog_id for p in products):
            selected = catalog.get_product(s.database_path, catalog_id)
            if selected:
                products.insert(0, selected)
        return render(request, 'create.html', active='create',
                      products=products,
                      series=creative_series.list_templates(s), selected_catalog_id=catalog_id,
                      selected_branch=branch if branch in {'product_review', 'viral_creator', 'template_series'} else 'product_review')

    @app.post('/api/create')
    def create(body: Creation):
        s = settings()
        options = {'creation_branch': body.creation_branch, 'experience': body.experience}
        topic = body.topic.strip()
        if body.creation_branch == 'product_review':
            product = catalog.get_product(s.database_path, body.catalog_id)
            if not product:
                raise HTTPException(400, 'Chọn sản phẩm trong kho để viết review.')
            offer, context = product_offer(s, product)
            options.update(offer=offer, product_context=context)
            topic = topic or product['name']
        elif body.creation_branch == 'template_series':
            try:
                options['template'] = creative_series.get_template(s, body.template_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        elif body.creation_branch != 'viral_creator':
            raise HTTPException(400, 'Nhánh sáng tạo không hợp lệ.')
        if not topic:
            raise HTTPException(400, 'Nhập chủ đề hoặc ý tưởng tập phim.')
        try:
            job_id = workflow.create_trend_job(s, topic, **options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.fast_mode:
            job = db.get_job(s.database_path, job_id)
            brief = json.loads(job.get('brief_json') or '{}')
            brief['fast_mode'] = True
            db.update_job(s.database_path, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
        db.enqueue_task(s.database_path, 'process', job_id)
        return {'job_id': job_id}

    @app.post('/api/creative-templates')
    def save_series(body: dict):
        try:
            return creative_series.save_template(settings(), body)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/api/creative-templates/{tid}/references')
    async def reference(tid: str, character_id: str = Form(...), rights: str = Form('owned'),
                        file: UploadFile = File(...)):
        content = await file.read(10 * 1024 * 1024 + 1)
        try:
            return creative_series.add_reference(settings(), tid, character_id,
                                                  file.filename or '', content, rights)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/jobs/{job_id}/affiliate')
    async def attach(job_id: str, request: Request):
        if request.headers.get('x-requested-with') != 'affiliate-agent':
            raise HTTPException(403, 'Thiếu mã xác nhận yêu cầu nội bộ.')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Yêu cầu khác nguồn bị từ chối.')
        body = await request.json()
        return workflow.attach_affiliate(settings(), job_id, str(body.get('catalog_id') or ''))


def storyboard(settings, job_id):
    """Only the existing job's bounded planning state; no arbitrary file route."""
    path = settings.work_dir / job_id / 'plan_state.json'
    root = settings.work_dir.resolve()
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return {}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        return state.get('plan') or {} if isinstance(state, dict) else {}
    except (ValueError, OSError):
        return {}
