"""Field-level comparison of published JSON evidence with a versioned numeric policy."""
import math

from .contracts import stable_digest

ABS_TOLERANCE = 1e-10
REL_TOLERANCE = 1e-9
_MISSING = object()


def _summary(value):
    if value is _MISSING:
        return {'present': False}
    if isinstance(value, (dict, list)) or isinstance(value, str) and len(value) > 256:
        return {'type': type(value).__name__, 'digest': stable_digest(value)}
    return value


def compare_product(expected, actual, *, on_difference=None):
    differences = []
    count = checked = 0

    def visit(left, right, path):
        nonlocal count, checked
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                visit(left.get(key, _MISSING), right.get(key, _MISSING),
                      path + '/' + key.replace('~', '~0').replace('/', '~1'))
            return
        if isinstance(left, list) and isinstance(right, list):
            for i in range(max(len(left), len(right))):
                visit(left[i] if i < len(left) else _MISSING, right[i] if i < len(right) else _MISSING,
                      f'{path}/{i}')
            return
        checked += 1
        if type(left) in (int, float) and type(right) in (int, float):
            same = (left == right if isinstance(left, int) else
                    math.isclose(left, right, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE))
        else:
            same = type(left) is type(right) and left == right
        if not same:
            count += 1
            if on_difference is not None or len(differences) < 24:
                difference = {'path': path or '/', 'expected': _summary(left), 'actual': _summary(right)}
                if on_difference is not None:
                    on_difference(difference)
                if len(differences) < 24:
                    differences.append(difference)

    visit(expected, actual, '')
    return {'status': 'DIFFERENT' if count else 'REPRODUCED',
            'expected_digest': stable_digest(expected), 'actual_digest': stable_digest(actual),
            'fields_checked': checked, 'difference_count': count, 'differences': differences,
            'abs_tolerance': ABS_TOLERANCE, 'rel_tolerance': REL_TOLERANCE}
