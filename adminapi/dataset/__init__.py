from distutils.util import strtobool
from itertools import chain

from adminapi.datatype import validate_value, json_to_datatype
from adminapi.filters import BaseFilter
from adminapi.request import send_request

COMMIT_ENDPOINT = '/dataset/commit'
QUERY_ENDPOINT = '/dataset/query'
CREATE_ENDPOINT = '/dataset/create'


class DatasetError(Exception):
    pass


class BaseQuery(object):
    def __init__(self, filters=None, restrict=None, order_by=None):
        if filters is None:
            self._filters = None
            self._results = []
        else:
            self._filters = {
                a: f if isinstance(f, BaseFilter) else BaseFilter(f)
                for a, f in filters.items()
            }
            self._results = None
        self._restrict = restrict
        self._order_by = order_by

    def __iter__(self):
        return iter(self.get_results())

    def __len__(self):
        return len(self.get_results())

    def __bool__(self):
        return bool(self.get_results())

    def __repr__(self):
        args = []
        if self._filters is not None:
            args.append(repr(self._filters))
        if self._restrict is not None:
            args.append('restrict=' + repr(self._restrict))
        if self._order_by is not None:
            args.append('order_by=' + repr(self._order_by))
        return 'Query({})'.format(', '.join(args))

    def get_lookup(self, attr):
        lookup = {}
        for host in self:
            if attr in host:
                lookup[host[attr]] = host
        return lookup

    # XXX: Deprecated
    def restrict(self, *attrs):
        if not attrs:
            return self

        if isinstance(attrs[0], (list, set, tuple)):
            attrs = attrs[0]

        self._restrict = {str(a) for a in attrs}

        return self

    # XXX: Deprecated
    def order_by(self, *attribute_ids):
        self._order_by = attribute_ids
        return self

    def get(self):
        results = self.get_results()
        if len(results) != 1:
            raise DatasetError('get() requires exactly 1 matched object')
        return results[0]

    def is_dirty(self):
        return any(s.is_dirty() for s in self)

    def commit(self, skip_validation, force_changes):
        raise NotImplementedError()

    def rollback(self):
        for obj in self:
            obj.rollback()
        return self

    def delete(self):
        for obj in self:
            obj.delete()
        return self

    def update(self, **attrs):
        for obj in self:
            if not obj.is_deleted():
                obj.update(attrs)
        return self

    def iterattrs(self, attr='hostname'):
        return (obj[attr] for obj in self)

    def get_results(self):
        raise NotImplementedError()

    def _confirm_changes(self):
        for obj in self:
            obj._confirm_changes()

    def _build_commit_object(self):
        commit = {
            'deleted': [],
            'changes': {},
        }

        for obj in self:
            if obj.is_deleted():
                commit['deleted'].append(obj.object_id)
            elif obj.is_dirty():
                commit['changes'][obj.object_id] = obj._serialize_changes()

        return commit


class Query(BaseQuery):

    def commit(self, skip_validation=False, force_changes=False):
        commit = self._build_commit_object()
        commit['skip_validation'] = skip_validation
        commit['force_changes'] = force_changes
        result = send_request(COMMIT_ENDPOINT, commit)

        if result['status'] == 'error':
            _handle_exception(result)

        self.num_dirty = 0
        for obj in self:
            obj._confirm_changes()

    def get_results(self):
        if self._results is None:
            request_data = {'filters': self._filters}
            if self._restrict is not None:
                request_data['restrict'] = self._restrict
            if self._order_by is not None:
                request_data['order_by'] = self._order_by

            response = send_request(QUERY_ENDPOINT, request_data)
            if response['status'] == 'error':
                _handle_exception(response)
            self._results = [_format_obj(s) for s in response['result']]
        return self._results


class DatasetObject(dict):
    """This class must redefine all mutable methods of the dict class
    to cast multi attributes and to validate the values.
    """

    def __init__(self, attributes=[], object_id=None):
        # Loop through ourself afterwards would be more efficient, but
        # this would give the caller already initialised object in case
        # anything fails.
        attributes = dict(attributes)
        for attribute_id, value in attributes.items():
            if isinstance(value, (tuple, list, set, frozenset)):
                attributes[attribute_id] = MultiAttr(value, self, attribute_id)
        super(DatasetObject, self).__init__(attributes)
        self.object_id = object_id
        self._deleted = False
        self.old_values = {}

    def __hash__(self):
        """Make the objects hashable

        Note that it will not work for objects which doesn't have object_id.
        It is the callers responsibility not to use the them in hashable
        context.  We could try harder to make them hashable with a fallback
        method, but that would lead them to considered as different objects
        after they got an object_id.
        """

        return self.object_id

    def __repr__(self):
        parent_repr = super(DatasetObject, self).__repr__()
        if not self.object_id:
            return 'DatasetObject({0})'.format(parent_repr)
        return 'DatasetObject({0}, {1})'.format(parent_repr, self.object_id)

    def is_dirty(self):
        if self.object_id is None:
            return True
        if self._deleted:
            return True
        for attribute_id, old_value in self.old_values.items():
            if self[attribute_id] != old_value:
                return True
        return False

    def is_deleted(self):
        return self._deleted

    # XXX: Deprecated
    def rollback(self):
        self._deleted = False
        for attr, old_value in self.old_values.items():
            super(DatasetObject, self).__setitem__(attr, old_value)
        self.old_values.clear()

    def delete(self):
        self._deleted = True

    def _serialize_changes(self):
        changes = {}
        for key, old_value in self.old_values.items():
            new_value = self[key]
            if isinstance(old_value, MultiAttr):
                action = 'multi'
            else:
                action = 'update'

            change = {'action': action}
            if action == 'update':
                change['old'] = old_value
                change['new'] = new_value
            elif action == 'multi':
                change['remove'] = old_value.difference(new_value)
                change['add'] = new_value.difference(old_value)

            changes[key] = change

        return changes

    def _confirm_changes(self):
        self.old_values.clear()
        if self._deleted:
            self.object_id = None
            self._deleted = False

    def _build_commit_object(self):
        changes = {}
        if self.is_dirty():
            changes[self.object_id] = self._serialize_changes()
        return {
            'deleted': [self.object_id] if self.is_deleted() else [],
            'changes': changes,
        }

    def _save_old_value(self, key):
        # We need to save the first version only.
        if key not in self.old_values:
            old_value = self[key]
            if isinstance(old_value, MultiAttr):
                self.old_values[key] = old_value.copy()
            else:
                self.old_values[key] = old_value

    def __setitem__(self, key, value):
        if self._deleted:
            raise DatasetError('Cannot set attributes to deleted object')
        if key not in self:
            raise DatasetError(
                'Cannot set nonexistent attribute "{}"'.format(key)
            )

        self._save_old_value(key)
        self.validate(key, value)

        if isinstance(self[key], MultiAttr):
            value = MultiAttr(value, self, key)

        return super(DatasetObject, self).__setitem__(key, value)

    def validate(self, key, value):
        # Boolean attributes are guaranteed to exist as booleans, multi
        # attributes are guaranteed to exist as sets, so we can rely on
        # the existing value.
        old_value = self.old_values.get(key, self[key])
        datatype = None
        if isinstance(old_value, bool):
            if not isinstance(value, bool):
                raise TypeError('Attribute "{}" must be a boolean'.format(key))
        elif isinstance(old_value, MultiAttr):
            if not isinstance(value, (tuple, list, set, frozenset)):
                raise TypeError('Attribute "{}" must be multi'.format(key))
            for elem in old_value | set(value):
                datatype = validate_value(elem, datatype)
        elif value is not None:
            if old_value is not None:
                datatype = type(old_value)
            validate_value(value, datatype)

    def set(self, key, value):
        if isinstance(self[key], MultiAttr):
            self[key].add(value)
        elif type(self[key]) is bool:
            self[key] = bool(strtobool(value))
        elif type(self[key]) is int:
            self[key] = int(value)
        else:
            self[key] = value

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, other, **kwargs):
        if hasattr(other, 'items'):
            other = other.items()
        for key, value in chain(other, kwargs.items()):
            self[key] = value

    # XXX: Deprecated
    def commit(self, skip_validation=False, force_changes=False):
        commit = self._build_commit_object()
        commit['skip_validation'] = skip_validation
        commit['force_changes'] = force_changes
        result = send_request(COMMIT_ENDPOINT, commit)

        if result['status'] == 'error':
            _handle_exception(result)

        self._confirm_changes()


class MultiAttr(set):
    """This class must redefine all mutable methods of the set class
    to maintain the old values on the DatasetObject.
    """

    def __init__(self, other, obj, attribute_id):
        super(MultiAttr, self).__init__(other)
        self._obj = obj
        self._attribute_id = attribute_id

    def __str__(self):
        return ' '.join(str(x) for x in self)

    def copy(self):
        return MultiAttr(self, self._obj, self._attribute_id)

    def add(self, elem):
        self._obj[self._attribute_id] = self | {elem}

    def discard(self, elem):
        self._obj[self._attribute_id] = self - {elem}

    def remove(self, elem):
        if elem not in self:
            raise KeyError()
        self.discard(elem)

    def pop(self):
        for elem in self:
            break
        else:
            raise KeyError()
        self.discard(elem)
        return elem

    def clear(self):
        self._obj[self._attribute_id] = set()

    def update(self, *others):
        new = set(self)
        for other in others:
            new |= other
        self._obj[self._attribute_id] = new

    def intersection_update(self, *others):
        new = set(self)
        for other in others:
            new &= other
        self._obj[self._attribute_id] = new

    def difference_update(self, *others):
        new = set(self)
        for other in others:
            new -= other
        self._obj[self._attribute_id] = new

    def symmetric_difference_update(self, other):
        self._obj[self._attribute_id] = self ^ other


# XXX: Deprecated
def _handle_exception(result):
    if result['type'] == 'ValueError':
        exception_class = ValueError
    else:
        exception_class = DatasetError

    #
    # Dear traceback reader,
    #
    # This is not the location of the exception, please read the
    # exception message and figure out what's wrong with your
    # code.
    #
    raise exception_class(result['message'])


# XXX Deprecated, use Query() instead
def query(**kwargs):
    return Query(kwargs)


def create(
    attributes,
    skip_validation=False,
    fill_defaults=True,
    fill_defaults_all=False,
):
    request = {
        'attributes': attributes,
        'skip_validation': skip_validation,
        'fill_defaults': fill_defaults,
        'fill_defaults_all': fill_defaults_all,
    }

    response = send_request(CREATE_ENDPOINT, request)
    if response['status'] == 'error':
        _handle_exception(response)

    return _format_obj(response['result'][0])


def _format_obj(result):
    object_id = result['object_id']
    obj = DatasetObject([], object_id)

    for attribute_id, value in list(result.items()):
        if isinstance(value, list):
            casted_value = MultiAttr(
                (_format_attribute_value(v) for v in value),
                obj,
                attribute_id,
            )
        else:
            casted_value = _format_attribute_value(value)

        dict.__setitem__(obj, attribute_id, casted_value)

    return obj


def _format_attribute_value(value):
    if isinstance(value, dict):
        return _format_obj(value)
    return json_to_datatype(value)
