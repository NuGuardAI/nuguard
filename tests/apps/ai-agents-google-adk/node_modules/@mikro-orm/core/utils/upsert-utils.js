"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getOnConflictFields = getOnConflictFields;
exports.getOnConflictReturningFields = getOnConflictReturningFields;
exports.getWhereCondition = getWhereCondition;
const Utils_1 = require("./Utils");
function expandEmbeddedProperties(prop, key) {
    if (prop.object) {
        return [prop.name];
    }
    return Object.values(prop.embeddedProps).flatMap(p => {
        /* istanbul ignore next */
        if (p.embeddable && !p.object) {
            return expandEmbeddedProperties(p);
        }
        return [p.name];
    });
}
/**
 * Expands dot paths and stars
 */
function expandFields(meta, fields) {
    return fields.flatMap(f => {
        if (f === '*' && meta) {
            return meta.comparableProps.filter(p => !p.lazy && !p.embeddable).map(p => p.name);
        }
        if (f.includes('.')) {
            const [k, ...tmp] = f.split('.');
            const rest = tmp.join('.');
            const prop = meta?.properties[k];
            if (prop?.embeddable) {
                if (rest === '*') {
                    return expandEmbeddedProperties(prop);
                }
                return expandEmbeddedProperties(prop, rest);
            }
        }
        const prop = meta?.properties[f];
        if (prop?.embeddable) {
            return expandEmbeddedProperties(prop);
        }
        return [f];
    });
}
/** @internal */
function getOnConflictFields(meta, data, uniqueFields, options) {
    if (options.onConflictMergeFields) {
        const onConflictMergeFields = expandFields(meta, options.onConflictMergeFields);
        return onConflictMergeFields.flatMap(f => {
            const prop = meta?.properties[f];
            /* istanbul ignore next */
            if (prop?.embeddable && !prop.object) {
                return Object.values(prop.embeddedProps).map(p => p.name);
            }
            return f;
        });
    }
    const keys = Object.keys(data).flatMap(f => {
        if (!(Array.isArray(uniqueFields) && !uniqueFields.includes(f))) {
            return [];
        }
        const prop = meta?.properties[f];
        if (prop?.embeddable && !prop.object) {
            return expandEmbeddedProperties(prop);
        }
        return [f];
    });
    if (options.onConflictExcludeFields) {
        const onConflictExcludeFields = expandFields(meta, options.onConflictExcludeFields);
        return keys.filter(f => !onConflictExcludeFields.includes(f));
    }
    return keys;
}
/** @internal */
function getOnConflictReturningFields(meta, data, uniqueFields, options) {
    /* istanbul ignore next */
    if (!meta) {
        return '*';
    }
    const keys = meta.comparableProps.filter(p => {
        if (p.lazy || p.embeddable) {
            return false;
        }
        if (p.autoincrement) {
            return true;
        }
        return Array.isArray(uniqueFields) && !uniqueFields.includes(p.name);
    }).map(p => p.name);
    if (meta.versionProperty) {
        keys.push(meta.versionProperty);
    }
    if (options.onConflictAction === 'ignore') {
        return keys;
    }
    if (options.onConflictMergeFields) {
        const onConflictMergeFields = expandFields(meta, options.onConflictMergeFields);
        return keys.filter(key => !onConflictMergeFields.includes(key));
    }
    if (options.onConflictExcludeFields) {
        const onConflictExcludeFields = expandFields(meta, options.onConflictExcludeFields);
        return [...new Set(keys.concat(...onConflictExcludeFields))];
    }
    return keys.filter(key => !(key in data));
}
function getPropertyValue(obj, key) {
    if (key.indexOf('.') === -1) {
        return obj[key];
    }
    const parts = key.split('.');
    let curr = obj;
    for (let i = 0; i < parts.length - 1; i++) {
        curr[parts[i]] ??= {};
        curr = curr[parts[i]];
    }
    return curr[parts[parts.length - 1]];
}
/** @internal */
function getWhereCondition(meta, onConflictFields, data, where) {
    const unique = onConflictFields ?? meta.props.filter(p => p.unique).map(p => p.name);
    const propIndex = !Utils_1.Utils.isRawSql(unique) && unique.findIndex(p => data[p] ?? data[p.substring(0, p.indexOf('.'))] != null);
    if (onConflictFields || where == null) {
        if (propIndex !== false && propIndex >= 0) {
            let key = unique[propIndex];
            if (key.includes('.')) {
                const prop = meta.properties[key.substring(0, key.indexOf('.'))];
                if (prop) {
                    key = `${prop.fieldNames[0]}${key.substring(key.indexOf('.'))}`;
                }
            }
            where = { [key]: getPropertyValue(data, unique[propIndex]) };
        }
        else if (meta.uniques.length > 0) {
            for (const u of meta.uniques) {
                if (Utils_1.Utils.asArray(u.properties).every(p => data[p] != null)) {
                    where = Utils_1.Utils.asArray(u.properties).reduce((o, key) => {
                        o[key] = data[key];
                        return o;
                    }, {});
                    break;
                }
            }
        }
    }
    return { where, propIndex };
}
