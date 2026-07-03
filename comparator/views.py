import json
import logging
import os
import string

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .services.correspondence import find_correspondences
from .services.file_diff import compute_file_diff

logger = logging.getLogger(__name__)


@require_GET
def index(request):
    """Render the single-page comparator UI."""
    response = render(request, 'comparator/index.html')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@csrf_exempt
@require_POST
def compare(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    left_dir = body.get('left_dir', '').strip()
    right_dir = body.get('right_dir', '').strip()

    if not left_dir or not right_dir:
        return JsonResponse(
            {'error': 'Both left_dir and right_dir are required'},
            status=400,
        )

    try:
        result = find_correspondences(left_dir, right_dir)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Comparison failed: %s", exc)
        return JsonResponse(
            {'error': f'Comparison failed: {exc}'},
            status=500,
        )

    return JsonResponse({
        'matched': [
            {
                'left': m.left_file.to_dict(),
                'right': m.right_file.to_dict(),
                'match_type': m.match_type,
                'similarity': m.similarity,
                'content_status': m.content_status,
            }
            for m in result.matched
        ],
        'unmatched_left': [f.to_dict() for f in result.unmatched_left],
        'unmatched_right': [f.to_dict() for f in result.unmatched_right],
        'stats': result.stats,
    })


@require_GET
def browse(request):
    """
    GET /api/browse/?path=D:\\Proyectos
    Returns JSON listing of subdirectories at the given path.
    If no path is given, returns available drive letters (Windows)
    or the filesystem root (Unix).
    """
    req_path = request.GET.get('path', '').strip()

    if not req_path:
        if os.name == 'nt':
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.isdir(drive):
                    drives.append({
                        'name': f"{letter}:",
                        'path': drive,
                    })
            return JsonResponse({'entries': drives, 'current': ''})
        else:
            req_path = '/'

    req_path = os.path.normpath(req_path)

    if not os.path.isdir(req_path):
        return JsonResponse({'error': f'Not a directory: {req_path}'}, status=400)

    entries = []
    try:
        for entry in sorted(os.scandir(req_path), key=lambda e: e.name.lower()):
            if not entry.is_dir():
                continue
            if entry.name.startswith('.'):
                continue
            if entry.name.lower() in {
                'node_modules', '__pycache__', '$recycle.bin',
                'system volume information', 'recovery',
            }:
                continue
            entries.append({
                'name': entry.name,
                'path': entry.path.replace('\\', '/'),
            })
    except PermissionError:
        return JsonResponse({'error': f'Permission denied: {req_path}'}, status=403)

    parent = os.path.dirname(req_path)
    parent = parent.replace('\\', '/') if parent != req_path else ''

    return JsonResponse({
        'entries': entries,
        'current': req_path.replace('\\', '/'),
        'parent': parent,
    })


@require_GET
def file_compare(request):
    """Render the file comparison page."""
    left_path = request.GET.get('left', '').strip()
    right_path = request.GET.get('right', '').strip()
    unmatched_side = request.GET.get('unmatched', '').strip()
    response = render(request, 'comparator/file_compare.html', {
        'left_path': left_path,
        'right_path': right_path,
        'unmatched_side': unmatched_side,
    })
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@require_GET
def file_diff(request):
    """
    GET /api/file-diff/?left=<path>&right=<path>
    GET /api/file-diff/?left=<path>&unmatched=left   (single-file mode)
    GET /api/file-diff/?right=<path>&unmatched=right  (single-file mode)
    Returns JSON with line-level diff data.
    """
    left_path = request.GET.get('left', '').strip()
    right_path = request.GET.get('right', '').strip()
    unmatched_side = request.GET.get('unmatched', '').strip()

    # Single-file mode for unmatched files
    if unmatched_side in ('left', 'right'):
        file_path = left_path if unmatched_side == 'left' else right_path
        if not file_path:
            return JsonResponse(
                {'error': 'File path is required for unmatched view'},
                status=400,
            )
        if not os.path.isfile(file_path):
            return JsonResponse(
                {'error': f'File not found: {file_path}'},
                status=400,
            )
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
        except Exception as exc:
            logger.exception("Failed to read file: %s", exc)
            return JsonResponse(
                {'error': f'Failed to read file: {exc}'},
                status=500,
            )
        return JsonResponse({
            'left_lines': lines if unmatched_side == 'left' else [],
            'right_lines': lines if unmatched_side == 'right' else [],
            'opcodes': [],
            'minor_flags': [],
            'left_path': left_path,
            'right_path': right_path,
            'unmatched': unmatched_side,
        })

    if not left_path or not right_path:
        return JsonResponse(
            {'error': 'Both left and right file paths are required'},
            status=400,
        )

    for label, path in [('Left', left_path), ('Right', right_path)]:
        if not os.path.isfile(path):
            return JsonResponse(
                {'error': f'{label} file not found: {path}'},
                status=400,
            )

    try:
        result = compute_file_diff(left_path, right_path)
    except Exception as exc:
        logger.exception("File diff failed: %s", exc)
        return JsonResponse(
            {'error': f'Diff computation failed: {exc}'},
            status=500,
        )

    return JsonResponse(result)
