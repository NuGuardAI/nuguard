"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MetadataProvider = void 0;
const Utils_1 = require("../utils/Utils");
class MetadataProvider {
    config;
    constructor(config) {
        this.config = config;
    }
    loadFromCache(meta, cache) {
        Object.values(cache.properties).forEach(prop => {
            const metaProp = meta.properties[prop.name];
            if (metaProp?.enum && Array.isArray(metaProp.items)) {
                delete prop.items;
            }
        });
        // Preserve function expressions from indexes/uniques — they can't survive JSON cache serialization
        const expressionMap = new Map();
        for (const idx of [...(meta.indexes ?? []), ...(meta.uniques ?? [])]) {
            if (typeof idx.expression === 'function' && idx.name) {
                expressionMap.set(idx.name, idx.expression);
            }
        }
        Utils_1.Utils.mergeConfig(meta, cache);
        // Restore function expressions that were lost during JSON serialization
        if (expressionMap.size > 0) {
            for (const idx of [...(meta.indexes ?? []), ...(meta.uniques ?? [])]) {
                const fn = idx.name && expressionMap.get(idx.name);
                if (fn && typeof idx.expression !== 'function') {
                    idx.expression = fn;
                }
            }
        }
    }
    useCache() {
        return this.config.get('metadataCache').enabled ?? false;
    }
}
exports.MetadataProvider = MetadataProvider;
