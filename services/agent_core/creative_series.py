"""Versioned owner-managed recurring casts and immutable reference assets."""
import copy
import hashlib
import json
import os
import re
import struct
import threading
import uuid
import zlib
from pathlib import Path

_LOCK = threading.RLock()
_ID = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_RIGHTS = {'owned', 'licensed', 'permission', 'official'}
_BUILTIN = {
    'id': 'suri-family', 'label': 'Gia đình Suri', 'version': 1,
    'style': 'Hoạt hình chibi 3D dễ thương: đầu to, thân nhỏ tròn, mắt to biểu cảm, chất liệu mềm, '
             'ánh sáng ấm; hài gia đình ấm áp, nhịp kể rõ ràng, giữ tạo hình nhất quán qua các tập.',
    'setting': 'Gia đình Việt hiện đại trong ngôi nhà ấm cúng; không gian sinh hoạt đời thường.',
    'characters': [
        {'id': 'suri', 'name': 'Suri', 'age': 4, 'gender': 'female', 'role': 'daughter',
         'description': 'Bé gái 4 tuổi tò mò, lanh lợi, tinh nghịch đáng yêu; phản ứng hồn nhiên.',
         'personality': 'Hồn nhiên, hay hỏi "tại sao", lý sự đáng yêu, hay mặc cả với bố mẹ.',
         'voice': 'Ái Hân'},
        {'id': 'mother', 'name': 'Mẹ Suri', 'age': 30, 'gender': 'female', 'role': 'mother',
         'description': 'Người mẹ gần gũi, nhanh trí, biểu cảm hài hước và ấm áp.',
         'personality': 'Dịu dàng nhưng nghiêm khi cần, nói ngắn gọn, hay trêu bố.', 'voice': 'Mỹ Duyên'},
        {'id': 'father', 'name': 'Bố Suri', 'age': 32, 'gender': 'male', 'role': 'father',
         'description': 'Người bố vui tính, hơi vụng về, quan tâm gia đình.',
         'personality': 'Tự tin thái quá, kế hoạch hay hỏng một cách vô hại, chiều con.', 'voice': 'Mạnh Dũng'},
    ],
    'style_profile': 'chibi_3d',
    'script_notes': 'Hài gia đình ấm áp: một tình huống đời thường, thoại qua lại nhanh, cao trào nhỏ, '
                    'kết bằng câu chốt dễ thương hoặc bài học nhẹ.',
    'continuity_rules': ['Giữ nguyên danh tính, tuổi, khuôn mặt và tỷ lệ cơ thể từng nhân vật.',
                         'Trang phục, giọng nói và bối cảnh nhất quán trong cùng một tập.',
                         'Không tự nhận ảnh tham khảo đã được cung cấp khi kho tham khảo trống.'],
    'reference_assets': [],
    'voice_mode': 'dialogue',   # family sitcom: the characters talk to each other (VieNeu voices)
}
_TEXT = {'gender': 20, 'role': 60, 'personality': 1000, 'appearance': 1000, 'wardrobe': 600}


def _identifier(value):
    value = str(value or '')
    if not _ID.fullmatch(value):
        raise ValueError('ID chỉ gồm chữ thường, số, dấu gạch; bắt đầu bằng chữ.')
    return value


def _path(settings):
    return Path(settings.project_root) / 'state' / 'creative_templates.json'


def _load(settings):
    path = _path(settings)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schemaVersion') != 1 or not isinstance(data.get('templates'), dict):
        raise ValueError('Kho mẫu không đúng định dạng; giữ nguyên tệp để khôi phục.')
    return data['templates']


def _write(settings, templates):
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump({'schemaVersion': 1, 'templates': templates}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def list_templates(settings):
    with _LOCK:
        templates = {'suri-family': _BUILTIN, **_load(settings)}
        return [_with_mode(t) for t in templates.values()]


def get_template(settings, tid):
    tid = _identifier(tid)
    with _LOCK:
        data = _load(settings).get(tid)
        if data is None and tid == 'suri-family':
            data = _BUILTIN
        if data is None:
            raise ValueError('Không tìm thấy mẫu nhân vật.')
        return _with_mode(data)


def _with_mode(template):
    """Templates saved before voice modes existed: the family sitcom talks, others narrate."""
    data = copy.deepcopy(template)
    data.setdefault('voice_mode', 'dialogue' if data.get('id') == 'suri-family' else 'narration')
    if data.get('id') == 'suri-family':   # a Suri saved before voices/personality existed: fill them in
        for key in ('style_profile', 'script_notes'):
            data.setdefault(key, _BUILTIN[key])
        builtin = {c['id']: c for c in _BUILTIN['characters']}
        for c in data.get('characters') or []:
            for key in ('gender', 'role', 'personality', 'voice'):
                if not c.get(key) and builtin.get(c.get('id'), {}).get(key):
                    c[key] = builtin[c['id']][key]
    return data


def _validate(data):
    result = {'id': _identifier(data.get('id'))}
    for field, bound in [('label', 160), ('style', 5000), ('setting', 3000)]:
        value = str(data.get(field) or '').strip()
        if not value or len(value) > bound:
            raise ValueError(f'{field} thiếu hoặc quá dài.')
        result[field] = value
    characters = data.get('characters')
    if not isinstance(characters, list) or not 1 <= len(characters) <= 6:
        raise ValueError('Mẫu cần từ 1 đến 6 nhân vật.')
    result['characters'] = []
    ids = set()
    for character in characters:
        cid = _identifier(character.get('id'))
        age = character.get('age')
        if cid in ids or isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 120:
            raise ValueError('ID nhân vật trùng hoặc tuổi không hợp lệ.')
        ids.add(cid)
        name, description = str(character.get('name') or '').strip(), str(character.get('description') or '').strip()
        if not name or len(name) > 160 or len(description) > 3000:
            raise ValueError('Tên/mô tả nhân vật thiếu hoặc quá dài.')
        item = {'id': cid, 'name': name, 'description': description, 'age': age}
        for key, bound in _TEXT.items():
            value = str(character.get(key) or '').strip()
            if len(value) > bound:
                raise ValueError(f'{key} của {name} quá dài.')
            if value:
                item[key] = value
        if not (description or item.get('personality') or item.get('appearance')):
            raise ValueError(f'Nhân vật {name}: cần mô tả, tính cách hoặc ngoại hình.')
        voice = str(character.get('voice') or '').strip()
        if voice:
            from .editor.tts import VOICES
            if voice not in VOICES:
                raise ValueError(f'Giọng "{voice}" không có trong danh sách giọng VieNeu.')
            item['voice'] = voice
        result['characters'].append(item)
    rules = data.get('continuity_rules') or []
    if not isinstance(rules, list) or len(rules) > 30 or any(not isinstance(r, str) or len(r) > 1000 for r in rules):
        raise ValueError('Quy tắc liên tục không hợp lệ.')
    result['continuity_rules'] = rules
    mode = data.get('voice_mode') or ('dialogue' if result['id'] == 'suri-family' else 'narration')
    if mode not in ('dialogue', 'narration'):
        raise ValueError('voice_mode phải là "dialogue" (hội thoại) hoặc "narration" (thuyết minh).')
    result['voice_mode'] = mode
    style = str(data.get('style_profile') or '').strip()
    if style:
        from .style_profiles import PROFILES
        if style not in PROFILES:
            raise ValueError(f'Phong cách "{style}" không có.')
        result['style_profile'] = style
    notes = str(data.get('script_notes') or '').strip()
    if len(notes) > 3000:
        raise ValueError('Kịch bản/giọng điệu chung quá dài (tối đa 3000 ký tự).')
    if notes:
        result['script_notes'] = notes
    return result


def save_template(settings, data):
    validated = _validate(data)
    with _LOCK:
        templates = _load(settings)
        previous = templates.get(validated['id']) or (_BUILTIN if validated['id'] == 'suri-family' else {})
        refs = copy.deepcopy(previous.get('reference_assets') or [])
        known = {c['id'] for c in validated['characters']}
        if any(r['character_id'] not in known for r in refs):
            raise ValueError('Không thể bỏ nhân vật đang có ảnh tham khảo.')
        validated.update(version=int(previous.get('version', 0)) + 1, reference_assets=refs)
        templates[validated['id']] = validated
        _write(settings, templates)
        return copy.deepcopy(validated)


def _image_type(content):
    if not isinstance(content, bytes) or not 16 <= len(content) <= 10 * 1024 * 1024:
        raise ValueError('Ảnh cần có kích thước dưới 10 MB.')
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        offset, saw_header, saw_data = 8, False, False
        while offset + 12 <= len(content):
            size = struct.unpack('>I', content[offset:offset + 4])[0]
            end = offset + 12 + size
            if end > len(content):
                break
            kind, payload = content[offset + 4:offset + 8], content[offset + 8:end - 4]
            if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack('>I', content[end - 4:end])[0]:
                break
            if not saw_header:
                if kind != b'IHDR' or len(payload) != 13:
                    break
                w, h = struct.unpack('>II', payload[:8])
                if not 0 < w <= 8192 or not 0 < h <= 8192:
                    break
                saw_header = True
            saw_data |= kind == b'IDAT' and bool(payload)
            if kind == b'IEND' and not payload and end == len(content) and saw_data:
                return '.png'
            offset = end
    elif content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9'):
        # Require a start-of-frame segment, not merely a renamed SOI/EOI blob.
        if any(bytes((255, marker)) in content for marker in (192, 193, 194)):
            return '.jpg'
    elif (content.startswith(b'RIFF') and content[8:12] == b'WEBP' and
          struct.unpack('<I', content[4:8])[0] + 8 == len(content) and
          content[12:16] in {b'VP8 ', b'VP8L', b'VP8X'}):
        return '.webp'
    raise ValueError('Ảnh phải là tệp PNG, JPEG hoặc WebP hợp lệ.')


def add_reference(settings, tid, character_id, filename, content, rights='owned'):
    tid, character_id = _identifier(tid), _identifier(character_id)
    if rights not in _RIGHTS:
        raise ValueError('Cần xác nhận quyền sử dụng ảnh tham khảo.')
    if Path(str(filename)).name != str(filename) or '/' in str(filename) or '\\' in str(filename):
        raise ValueError('Tên tệp không hợp lệ.')
    suffix = _image_type(content)
    if Path(filename).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        raise ValueError('Phần mở rộng ảnh không hợp lệ.')
    digest = hashlib.sha256(content).hexdigest()
    with _LOCK:
        template = get_template(settings, tid)
        if character_id not in {c['id'] for c in template['characters']}:
            raise ValueError('Nhân vật không thuộc mẫu.')
        root = Path(settings.assets).resolve() / 'creative_series'
        folder = (root / tid).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('Đường dẫn ảnh không hợp lệ.')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + suffix)
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Ảnh tham khảo đã có nhưng không khớp mã kiểm tra.')
        ref = {'id': character_id + '-' + digest[:16], 'character_id': character_id,
               'path': str(path), 'sha256': digest, 'rights': rights}
        refs = template['reference_assets']
        if any(r['id'] == ref['id'] for r in refs):
            return copy.deepcopy(template)
        templates = _load(settings)
        template['reference_assets'] = [*refs, ref]
        template['version'] += 1
        templates[tid] = template
        _write(settings, templates)
        return copy.deepcopy(template)
