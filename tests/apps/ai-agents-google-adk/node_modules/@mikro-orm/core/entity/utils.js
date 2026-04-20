"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.expandDotPaths = expandDotPaths;
exports.getLoadingStrategy = getLoadingStrategy;
const enums_1 = require("../enums");
const Utils_1 = require("../utils/Utils");
/**
 * Expands `books.perex` like populate to use `children` array instead of the dot syntax
 */
function expandNestedPopulate(parentProp, parts, strategy, all) {
    const meta = parentProp.targetMeta;
    const field = parts.shift();
    const prop = meta.properties[field];
    const ret = { field, strategy, all };
    if (parts.length > 0) {
        ret.children = [expandNestedPopulate(prop, parts, strategy)];
    }
    return ret;
}
/**
 * @internal
 */
function expandDotPaths(meta, populate, normalized = false) {
    const ret = normalized ? populate : Utils_1.Utils.asArray(populate).map(field => {
        if (typeof field === 'string') {
            return { field };
        }
        /* istanbul ignore next */
        return typeof field === 'boolean' || field.field === enums_1.PopulatePath.ALL
            ? { all: !!field, field: meta.primaryKeys[0] }
            : field;
    });
    for (const p of ret) {
        if (!p.field.includes('.')) {
            continue;
        }
        const [f, ...parts] = p.field.split('.');
        p.field = f;
        p.children ??= [];
        const prop = meta.properties[p.field];
        if (parts[0] === enums_1.PopulatePath.ALL) {
            prop.targetMeta.props
                .filter(prop => prop.lazy || prop.kind !== enums_1.ReferenceKind.SCALAR)
                .forEach(prop => p.children.push({ field: prop.name, strategy: p.strategy }));
        }
        else if (prop.kind === enums_1.ReferenceKind.EMBEDDED) {
            const embeddedProp = Object.values(prop.embeddedProps).find(c => c.embedded[1] === parts[0]);
            ret.push({
                ...p,
                field: embeddedProp.name,
                children: parts.length > 1 ? [expandNestedPopulate(embeddedProp, parts.slice(1), p.strategy, p.all)] : [],
            });
            p.children.push(expandNestedPopulate(prop, parts, p.strategy, p.all));
        }
        else {
            p.children.push(expandNestedPopulate(prop, parts, p.strategy, p.all));
        }
    }
    return ret;
}
/**
 * Returns the loading strategy based on the provided hint.
 * If `BALANCED` strategy is used, it will return JOINED if the property is a to-one relation.
 * @internal
 */
function getLoadingStrategy(strategy, kind) {
    if (strategy === enums_1.LoadStrategy.BALANCED) {
        return [enums_1.ReferenceKind.MANY_TO_ONE, enums_1.ReferenceKind.ONE_TO_ONE].includes(kind)
            ? enums_1.LoadStrategy.JOINED
            : enums_1.LoadStrategy.SELECT_IN;
    }
    return strategy;
}
