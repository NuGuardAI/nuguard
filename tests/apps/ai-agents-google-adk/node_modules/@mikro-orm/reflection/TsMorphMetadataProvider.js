"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.TsMorphMetadataProvider = void 0;
const ts_morph_1 = require("ts-morph");
const core_1 = require("@mikro-orm/core");
class TsMorphMetadataProvider extends core_1.MetadataProvider {
    project;
    sources;
    useCache() {
        return this.config.get('metadataCache').enabled ?? true;
    }
    loadEntityMetadata(meta, name) {
        if (!meta.path) {
            return;
        }
        this.initProperties(meta);
    }
    getExistingSourceFile(path, ext, validate = true) {
        if (!ext) {
            return this.getExistingSourceFile(path, '.d.ts', false) || this.getExistingSourceFile(path, '.ts');
        }
        const tsPath = path.match(/.*\/[^/]+$/)[0].replace(/\.js$/, ext);
        return this.getSourceFile(tsPath, validate);
    }
    initProperties(meta) {
        // load types and column names
        for (const prop of Object.values(meta.properties)) {
            const type = this.extractType(prop);
            if (!type || this.config.get('discovery').alwaysAnalyseProperties) {
                this.initPropertyType(meta, prop);
            }
            prop.type = type || prop.type;
        }
    }
    extractType(prop) {
        if (core_1.Utils.isString(prop.entity)) {
            return prop.entity;
        }
        if (prop.entity) {
            return core_1.Utils.className(prop.entity());
        }
        return prop.type;
    }
    cleanUpTypeTags(type) {
        const genericTags = [/Opt<(.*?)>/, /Hidden<(.*?)>/, /RequiredNullable<(.*?)>/];
        const intersectionTags = [
            'Opt.Brand',
            'Hidden.Brand',
            'RequiredNullable.Brand',
        ];
        for (const tag of genericTags) {
            type = type.replace(tag, '$1');
        }
        for (const tag of intersectionTags) {
            type = type.replace(' & ' + tag, '');
            type = type.replace(tag + ' & ', '');
        }
        return type;
    }
    initPropertyType(meta, prop) {
        const { type: typeRaw, optional } = this.readTypeFromSource(meta, prop);
        prop.type = this.cleanUpTypeTags(typeRaw);
        if (optional) {
            prop.optional = true;
        }
        this.processWrapper(prop, 'Ref');
        this.processWrapper(prop, 'Reference');
        this.processWrapper(prop, 'EntityRef');
        this.processWrapper(prop, 'ScalarRef');
        this.processWrapper(prop, 'ScalarReference');
        this.processWrapper(prop, 'Collection');
        prop.runtimeType ??= prop.type;
        if (prop.type.replace(/import\(.*\)\./g, '').match(/^(Dictionary|Record)<.*>$/)) {
            prop.type = 'json';
        }
    }
    readTypeFromSource(meta, prop) {
        const source = this.getExistingSourceFile(meta.path);
        const cls = source.getClass(meta.className);
        /* istanbul ignore next */
        if (!cls) {
            throw new core_1.MetadataError(`Source class for entity ${meta.className} not found. Verify you have 'compilerOptions.declaration' enabled in your 'tsconfig.json'. If you are using webpack, see https://bit.ly/35pPDNn`);
        }
        const properties = cls.getInstanceProperties();
        const property = properties.find(v => {
            if (v.getName() === prop.name) {
                return true;
            }
            const nameNode = v.getNameNode();
            if (nameNode instanceof ts_morph_1.StringLiteral && nameNode.getLiteralText() === prop.name) {
                return true;
            }
            if (nameNode instanceof ts_morph_1.ComputedPropertyName) {
                const expr = nameNode.getExpression();
                if (expr instanceof ts_morph_1.NoSubstitutionTemplateLiteral && expr.getLiteralText() === prop.name) {
                    return true;
                }
            }
            return false;
        });
        if (!property) {
            return { type: prop.type, optional: prop.nullable };
        }
        const tsType = property.getType();
        const typeName = tsType.getText(property);
        if (prop.enum && tsType.isEnum()) {
            prop.items = tsType.getUnionTypes().map(t => t.getLiteralValueOrThrow());
        }
        if (tsType.isArray()) {
            prop.array = true;
            /* istanbul ignore else */
            if (tsType.getArrayElementType().isEnum()) {
                prop.items = tsType.getArrayElementType().getUnionTypes().map(t => t.getLiteralValueOrThrow());
            }
        }
        if (prop.array && prop.enum) {
            prop.enum = false;
        }
        let type = typeName;
        const union = type.split(' | ');
        const optional = property.hasQuestionToken?.() || union.includes('null') || union.includes('undefined') || tsType.isNullable();
        type = union.filter(t => !['null', 'undefined'].includes(t)).join(' | ');
        prop.array ??= type.endsWith('[]') || !!type.match(/Array<(.*)>/);
        type = type
            .replace(/Array<(.*)>/, '$1') // unwrap array
            .replace(/\[]$/, '') // remove array suffix
            .replace(/\((.*)\)/, '$1'); // unwrap union types
        // keep the array suffix in the type, it is needed in few places in discovery and comparator (`prop.array` is used only for enum arrays)
        if (prop.array && !type.includes(' | ') && prop.kind === core_1.ReferenceKind.SCALAR) {
            type += '[]';
        }
        return { type, optional };
    }
    getSourceFile(tsPath, validate) {
        if (!this.sources) {
            this.initSourceFiles();
        }
        const baseDir = this.config.get('baseDir');
        const outDir = this.project.getCompilerOptions().outDir;
        let path = tsPath;
        if (outDir != null) {
            const outDirRelative = core_1.Utils.relativePath(outDir, baseDir);
            path = path.replace(new RegExp(`^${outDirRelative}`), '');
        }
        path = core_1.Utils.stripRelativePath(path);
        const source = this.sources.find(s => s.getFilePath().endsWith(path));
        if (!source && validate) {
            throw new core_1.MetadataError(`Source file '${tsPath}' not found. Check your 'entitiesTs' option and verify you have 'compilerOptions.declaration' enabled in your 'tsconfig.json'. If you are using webpack, see https://bit.ly/35pPDNn`);
        }
        return source;
    }
    processWrapper(prop, wrapper) {
        // type can be sometimes in form of:
        // `'({ object?: Entity | undefined; } & import("...").Reference<Entity>)'`
        // `{ object?: import("...").Entity | undefined; } & import("...").Reference<Entity>`
        // `{ node?: ({ id?: number | undefined; } & import("...").Reference<import("...").Entity>) | undefined; } & import("...").Reference<Entity>`
        // the regexp is looking for the `wrapper`, possible prefixed with `.` or wrapped in parens.
        const type = prop.type
            .replace(/import\(.*\)\./g, '')
            .replace(/\{ .* } & ([\w &]+)/g, '$1');
        const m = type.match(new RegExp(`(?:^|[.( ])${wrapper}<(\\w+),?.*>(?:$|[) ])`));
        if (!m) {
            return;
        }
        prop.type = m[1];
        if (['Ref', 'Reference', 'EntityRef', 'ScalarRef', 'ScalarReference'].includes(wrapper)) {
            prop.ref = true;
        }
    }
    initProject() {
        const settings = core_1.ConfigurationLoader.getSettings();
        /* istanbul ignore next */
        const tsConfigFilePath = this.config.get('discovery').tsConfigPath ?? settings.tsConfigPath ?? './tsconfig.json';
        try {
            this.project = new ts_morph_1.Project({
                tsConfigFilePath: core_1.Utils.normalizePath(process.cwd(), tsConfigFilePath),
                skipAddingFilesFromTsConfig: true,
                compilerOptions: {
                    strictNullChecks: true,
                    module: ts_morph_1.ModuleKind.Node16,
                },
            });
        }
        catch (e) {
            this.config.getLogger().warn('discovery', e.message);
            this.project = new ts_morph_1.Project({
                compilerOptions: {
                    strictNullChecks: true,
                    module: ts_morph_1.ModuleKind.Node16,
                },
            });
        }
    }
    initSourceFiles() {
        if (!this.project) {
            this.initProject();
        }
        this.sources = [];
        // All entity files are first required during the discovery, before we reach here, so it is safe to get the parts from the global
        // metadata storage. We know the path thanks to the decorators being executed. In case we are running via ts-node, the extension
        // will be already `.ts`, so no change is needed. `.js` files will get renamed to `.d.ts` files as they will be used as a source for
        // the ts-morph reflection.
        for (const meta of core_1.Utils.values(core_1.MetadataStorage.getMetadata())) {
            /* istanbul ignore next */
            const path = meta.path.match(/\.[jt]s$/)
                ? meta.path.replace(/\.js$/, '.d.ts')
                : `${meta.path}.d.ts`; // when entities are bundled, their paths are just their names
            const sourceFile = this.project.addSourceFileAtPathIfExists(path);
            if (sourceFile) {
                this.sources.push(sourceFile);
            }
        }
    }
}
exports.TsMorphMetadataProvider = TsMorphMetadataProvider;
