"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.p = exports.OneToManyOptionsBuilderOnlyMappedBy = exports.UniversalPropertyOptionsBuilder = void 0;
exports.defineEntity = defineEntity;
const types_1 = require("../types");
const EntitySchema_1 = require("../metadata/EntitySchema");
/** @internal */
class UniversalPropertyOptionsBuilder {
    '~options';
    '~type';
    constructor(options) {
        this['~options'] = options;
    }
    assignOptions(options) {
        return new UniversalPropertyOptionsBuilder({ ...this['~options'], ...options });
    }
    $type() {
        return this.assignOptions({});
    }
    /**
     * Alias for `fieldName`.
     */
    name(name) {
        return this.assignOptions({ name });
    }
    /**
     * Specify database column name for this property.
     *
     * @see https://mikro-orm.io/docs/naming-strategy
     */
    fieldName(fieldName) {
        return this.assignOptions({ fieldName });
    }
    /**
     * Specify database column names for this property.
     * Same as `fieldName` but for composite FKs.
     *
     * @see https://mikro-orm.io/docs/naming-strategy
     */
    fieldNames(...fieldNames) {
        return this.assignOptions({ fieldNames });
    }
    /**
     * Specify an exact database column type for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. This option is only for simple properties represented by a single column. (SQL only)
     */
    columnType(columnType) {
        return this.assignOptions({ columnType });
    }
    /**
     * Specify an exact database column type for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. This option is suitable for composite keys, where one property is represented by multiple columns. (SQL only)
     */
    columnTypes(...columnTypes) {
        return this.assignOptions({ columnTypes });
    }
    /**
     * Explicitly specify the runtime type.
     *
     * @see https://mikro-orm.io/docs/metadata-providers
     * @see https://mikro-orm.io/docs/custom-types
     */
    type(type) {
        return this.assignOptions({ type });
    }
    /**
     * Runtime type of the property. This is the JS type that your property is mapped to, e.g. `string` or `number`, and is normally inferred automatically via `reflect-metadata`.
     * In some cases, the inference won't work, and you might need to specify the `runtimeType` explicitly - the most common one is when you use a union type with null like `foo: number | null`.
     */
    runtimeType(runtimeType) {
        return this.assignOptions({ runtimeType });
    }
    /**
     * Set length of database column, used for datetime/timestamp/varchar column types for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. (SQL only)
     */
    length(length) {
        return this.assignOptions({ length });
    }
    /**
     * Set precision of database column to represent the number of significant digits. (SQL only)
     */
    precision(precision) {
        return this.assignOptions({ precision });
    }
    /**
     * Set scale of database column to represents the number of digits after the decimal point. (SQL only)
     */
    scale(scale) {
        return this.assignOptions({ scale });
    }
    /**
     * Explicitly specify the auto increment of the primary key.
     */
    autoincrement(autoincrement = true) {
        return this.assignOptions({ autoincrement });
    }
    /**
     * Add the property to the `returning` statement.
     */
    returning(returning = true) {
        return this.assignOptions({ returning });
    }
    /**
     * Automatically set the property value when entity gets created, executed during flush operation.
     */
    onCreate(onCreate) {
        return this.assignOptions({ onCreate });
    }
    /**
     * Automatically update the property value every time entity gets updated, executed during flush operation.
     */
    onUpdate(onUpdate) {
        return this.assignOptions({ onUpdate });
    }
    /**
     * Specify default column value for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}.
     * This is a runtime value, assignable to the entity property. (SQL only)
     */
    default(defaultValue) {
        return this.assignOptions({ default: defaultValue });
    }
    /**
     * Specify SQL functions for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. (SQL only)
     * Since v4 you should use defaultRaw for SQL functions. e.g. now()
     */
    defaultRaw(defaultRaw) {
        return this.assignOptions({ defaultRaw });
    }
    /**
     * Allow controlling `filters` option. This will be overridden with `em.fork` or `FindOptions` if provided.
     */
    filters(filters) {
        return this.assignOptions({ filters });
    }
    /**
     * Set to map some SQL snippet for the entity.
     *
     * @see https://mikro-orm.io/docs/defining-entities#formulas Formulas
     */
    formula(formula) {
        return this.assignOptions({ formula });
    }
    /**
     * For generated columns. This will be appended to the column type after the `generated always` clause.
     */
    generated(generated) {
        return this.assignOptions({ generated });
    }
    /**
     * Set column as nullable for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}.
     */
    nullable(nullable = true) {
        return this.assignOptions({ nullable });
    }
    /**
     * Set column as unsigned for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. (SQL only)
     */
    unsigned(unsigned = true) {
        return this.assignOptions({ unsigned });
    }
    /**
     * Set false to define {@link https://mikro-orm.io/docs/serializing#shadow-properties Shadow Property}.
     */
    persist(persist = true) {
        return this.assignOptions({ persist });
    }
    /**
     * Set false to disable hydration of this property. Useful for persisted getters.
     */
    hydrate(hydrate = true) {
        return this.assignOptions({ hydrate });
    }
    /**
     * Enable `ScalarReference` wrapper for lazy values. Use this in combination with `lazy: true` to have a type-safe accessor object in place of the value.
     */
    ref(ref = true) {
        return this.assignOptions({ ref });
    }
    /**
     * Set false to disable change tracking on a property level.
     *
     * @see https://mikro-orm.io/docs/unit-of-work#change-tracking-and-performance-considerations
     */
    trackChanges(trackChanges = true) {
        return this.assignOptions({ trackChanges });
    }
    /**
     * Set to true to omit the property when {@link https://mikro-orm.io/docs/serializing Serializing}.
     */
    hidden(hidden = true) {
        return this.assignOptions({ hidden });
    }
    /**
     * Set to true to enable {@link https://mikro-orm.io/docs/transactions#optimistic-locking Optimistic Locking} via version field. (SQL only)
     */
    version(version = true) {
        return this.assignOptions({ version });
    }
    /**
     * Set to true to enable {@link https://mikro-orm.io/docs/transactions#optimistic-locking Optimistic Locking} via concurrency fields.
     */
    concurrencyCheck(concurrencyCheck = true) {
        return this.assignOptions({ concurrencyCheck });
    }
    /**
     * Explicitly specify index on a property.
     */
    index(index = true) {
        return this.assignOptions({ index });
    }
    /**
     * Set column as unique for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. (SQL only)
     */
    unique(unique = true) {
        return this.assignOptions({ unique });
    }
    /**
     * Specify column with check constraints. (Postgres driver only)
     *
     * @see https://mikro-orm.io/docs/defining-entities#check-constraints
     */
    check(check) {
        return this.assignOptions({ check });
    }
    /**
     * Set to omit the property from the select clause for lazy loading.
     *
     * @see https://mikro-orm.io/docs/defining-entities#lazy-scalar-properties
     */
    lazy(lazy = true, ref = true) {
        return this.assignOptions({ lazy, ref });
    }
    /**
     * Set true to define entity's unique primary key identifier.
     *
     * @see https://mikro-orm.io/docs/decorators#primarykey
     */
    primary(primary = true) {
        return this.assignOptions({ primary });
    }
    /**
     * Set true to define the properties as setter. (virtual)
     *
     * @example
     * ```
     * @Property({ setter: true })
     * set address(value: string) {
     *     this._address = value.toLocaleLowerCase();
     * }
     * ```
     */
    setter(setter = true) {
        return this.assignOptions({ setter });
    }
    /**
     * Set true to define the properties as getter. (virtual)
     *
     * @example
     * ```
     * @Property({ getter: true })
     * get fullName() {
     *   return this.firstName + this.lastName;
     * }
     * ```
     */
    getter(getter = true) {
        return this.assignOptions({ getter });
    }
    /**
     * When defining a property over a method (not a getter, a regular function), you can use this option to point
     * to the method name.
     *
     * @example
     * ```
     * @Property({ getter: true })
     * getFullName() {
     *   return this.firstName + this.lastName;
     * }
     * ```
     */
    getterName(getterName) {
        return this.assignOptions({ getterName });
    }
    /**
     * Set to define serialized primary key for MongoDB. (virtual)
     * Alias for `@SerializedPrimaryKey()` decorator.
     *
     * @see https://mikro-orm.io/docs/decorators#serializedprimarykey
     */
    serializedPrimaryKey(serializedPrimaryKey = true) {
        return this.assignOptions({ serializedPrimaryKey });
    }
    /**
     * Set to use serialize property. Allow to specify a callback that will be used when serializing a property.
     *
     * @see https://mikro-orm.io/docs/serializing#property-serializers
     */
    serializer(serializer) {
        return this.assignOptions({ serializer });
    }
    /**
     * Specify name of key for the serialized value.
     */
    serializedName(serializedName) {
        return this.assignOptions({ serializedName });
    }
    /**
     * Specify serialization groups for `serialize()` calls. If a property does not specify any group, it will be included,
     * otherwise only properties with a matching group are included.
     */
    groups(...groups) {
        return this.assignOptions({ groups });
    }
    /**
     * Specify a custom order based on the values. (SQL only)
     */
    customOrder(...customOrder) {
        return this.assignOptions({ customOrder });
    }
    /**
     * Specify comment of column for {@link https://mikro-orm.io/docs/schema-generator Schema Generator}. (SQL only)
     */
    comment(comment) {
        return this.assignOptions({ comment });
    }
    /** mysql only */
    extra(extra) {
        return this.assignOptions({ extra });
    }
    /**
     * Set to avoid a perpetual diff from the {@link https://mikro-orm.io/docs/schema-generator Schema Generator} when columns are generated.
     *
     * @see https://mikro-orm.io/docs/defining-entities#sql-generated-columns
     */
    ignoreSchemaChanges(...ignoreSchemaChanges) {
        return this.assignOptions({ ignoreSchemaChanges });
    }
    array(array = true) {
        return this.assignOptions({ array });
    }
    /** for postgres, by default it uses text column with check constraint */
    nativeEnumName(nativeEnumName) {
        return this.assignOptions({ nativeEnumName });
    }
    prefix(prefix) {
        return this.assignOptions({ prefix });
    }
    prefixMode(prefixMode) {
        return this.assignOptions({ prefixMode });
    }
    object(object = true) {
        return this.assignOptions({ object });
    }
    /** Set what actions on owning entity should be cascaded to the relationship. Defaults to [Cascade.PERSIST, Cascade.MERGE] (see {@doclink cascading}). */
    cascade(...cascade) {
        return this.assignOptions({ cascade });
    }
    /** Always load the relationship. Discouraged for use with to-many relations for performance reasons. */
    eager(eager = true) {
        return this.assignOptions({ eager });
    }
    /** Override the default loading strategy for this property. This option has precedence over the global `loadStrategy`, but can be overridden by `FindOptions.strategy`. */
    strategy(strategy) {
        return this.assignOptions({ strategy });
    }
    /** Set this side as owning. Owning side is where the foreign key is defined. This option is not required if you use `inversedBy` or `mappedBy` to distinguish owning and inverse side. */
    owner(owner = true) {
        return this.assignOptions({ owner });
    }
    /** Point to the inverse side property name. */
    inversedBy(inversedBy) {
        return this.assignOptions({ inversedBy });
    }
    /** Point to the owning side property name. */
    mappedBy(mappedBy) {
        return this.assignOptions({ mappedBy });
    }
    /** Condition for {@doclink collections#declarative-partial-loading | Declarative partial loading}. */
    where(...where) {
        return this.assignOptions({ where });
    }
    /** Set default ordering. */
    orderBy(...orderBy) {
        return this.assignOptions({ orderBy });
    }
    /** Force stable insertion order of items in the collection (see {@doclink collections | Collections}). */
    fixedOrder(fixedOrder = true) {
        return this.assignOptions({ fixedOrder });
    }
    /** Override default order column name (`id`) for fixed ordering. */
    fixedOrderColumn(fixedOrderColumn) {
        return this.assignOptions({ fixedOrderColumn });
    }
    /** Override default name for pivot table (see {@doclink naming-strategy | Naming Strategy}). */
    pivotTable(pivotTable) {
        return this.assignOptions({ pivotTable });
    }
    /** Set pivot entity for this relation (see {@doclink collections#custom-pivot-table-entity | Custom pivot table entity}). */
    pivotEntity(pivotEntity) {
        return this.assignOptions({ pivotEntity });
    }
    /** Override the default database column name on the owning side (see {@doclink naming-strategy | Naming Strategy}). This option is only for simple properties represented by a single column. */
    joinColumn(joinColumn) {
        return this.assignOptions({ joinColumn });
    }
    /** Override the default database column name on the owning side (see {@doclink naming-strategy | Naming Strategy}). This option is suitable for composite keys, where one property is represented by multiple columns. */
    joinColumns(...joinColumns) {
        return this.assignOptions({ joinColumns });
    }
    /** Override the default database column name on the inverse side (see {@doclink naming-strategy | Naming Strategy}). This option is only for simple properties represented by a single column. */
    inverseJoinColumn(inverseJoinColumn) {
        return this.assignOptions({ inverseJoinColumn });
    }
    /** Override the default database column name on the inverse side (see {@doclink naming-strategy | Naming Strategy}). This option is suitable for composite keys, where one property is represented by multiple columns. */
    inverseJoinColumns(...inverseJoinColumns) {
        return this.assignOptions({ inverseJoinColumns });
    }
    /** Override the default database column name on the target entity (see {@doclink naming-strategy | Naming Strategy}). This option is only for simple properties represented by a single column. */
    referenceColumnName(referenceColumnName) {
        return this.assignOptions({ referenceColumnName });
    }
    /** Override the default database column name on the target entity (see {@doclink naming-strategy | Naming Strategy}). This option is suitable for composite keys, where one property is represented by multiple columns. */
    referencedColumnNames(...referencedColumnNames) {
        return this.assignOptions({ referencedColumnNames });
    }
    /** What to do when the target entity gets deleted. */
    deleteRule(deleteRule) {
        return this.assignOptions({ deleteRule });
    }
    /** What to do when the reference to the target entity gets updated. */
    updateRule(updateRule) {
        return this.assignOptions({ updateRule });
    }
    /** Map this relation to the primary key value instead of an entity. */
    mapToPk(mapToPk = true) {
        return this.assignOptions({ mapToPk });
    }
    /** Set the constraint type. Immediate constraints are checked for each statement, while deferred ones are only checked at the end of the transaction. Only for postgres unique constraints. */
    deferMode(deferMode) {
        return this.assignOptions({ deferMode });
    }
    /** When a part of a composite column is shared in other properties, use this option to specify what columns are considered as owned by this property. This is useful when your composite property is nullable, but parts of it are not. */
    ownColumns(...ownColumns) {
        return this.assignOptions({ ownColumns });
    }
    /** Enable/disable foreign key constraint creation on this relation */
    createForeignKeyConstraint(createForeignKeyConstraint = true) {
        return this.assignOptions({ createForeignKeyConstraint });
    }
    /** Set a custom foreign key constraint name, overriding NamingStrategy.indexName(). */
    foreignKeyName(foreignKeyName) {
        return this.assignOptions({ foreignKeyName });
    }
    /** Remove the entity when it gets disconnected from the relationship (see {@doclink cascading | Cascading}). */
    orphanRemoval(orphanRemoval = true) {
        return this.assignOptions({ orphanRemoval });
    }
    accessor(accessor = true) {
        return this.assignOptions({ accessor });
    }
}
exports.UniversalPropertyOptionsBuilder = UniversalPropertyOptionsBuilder;
/** @internal */
class OneToManyOptionsBuilderOnlyMappedBy extends UniversalPropertyOptionsBuilder {
    /** Point to the owning side property name. */
    mappedBy(mappedBy) {
        return new UniversalPropertyOptionsBuilder({ ...this['~options'], mappedBy });
    }
}
exports.OneToManyOptionsBuilderOnlyMappedBy = OneToManyOptionsBuilderOnlyMappedBy;
function createPropertyBuilders(options) {
    return Object.fromEntries(Object.entries(options).map(([key, value]) => [key, () => new UniversalPropertyOptionsBuilder({ type: value })]));
}
const propertyBuilders = {
    ...createPropertyBuilders(types_1.types),
    bigint: (mode) => new UniversalPropertyOptionsBuilder({ type: new types_1.types.bigint(mode) }),
    array: (toJsValue = i => i, toDbValue = i => i) => new UniversalPropertyOptionsBuilder({ type: new types_1.types.array(toJsValue, toDbValue) }),
    decimal: (mode) => new UniversalPropertyOptionsBuilder({ type: new types_1.types.decimal(mode) }),
    json: () => new UniversalPropertyOptionsBuilder({ type: types_1.types.json }),
    formula: (formula) => new UniversalPropertyOptionsBuilder({ formula }),
    datetime: (length) => new UniversalPropertyOptionsBuilder({ type: types_1.types.datetime, length }),
    time: (length) => new UniversalPropertyOptionsBuilder({ type: types_1.types.time, length }),
    type: (type) => new UniversalPropertyOptionsBuilder({ type }),
    enum: (items) => new UniversalPropertyOptionsBuilder({
        enum: true,
        items,
    }),
    embedded: (target) => new UniversalPropertyOptionsBuilder({
        entity: () => target,
        kind: 'embedded',
    }),
    manyToMany: (target) => new UniversalPropertyOptionsBuilder({
        entity: () => target,
        kind: 'm:n',
    }),
    manyToOne: (target) => new UniversalPropertyOptionsBuilder({
        entity: () => target,
        kind: 'm:1',
    }),
    oneToMany: (target) => new OneToManyOptionsBuilderOnlyMappedBy({
        entity: () => target,
        kind: '1:m',
    }),
    oneToOne: (target) => new UniversalPropertyOptionsBuilder({
        entity: () => target,
        kind: '1:1',
    }),
};
exports.p = propertyBuilders;
function getBuilderOptions(builder) {
    return '~options' in builder ? builder['~options'] : builder;
}
function defineEntity(meta) {
    const { properties: propertiesOrGetter, ...options } = meta;
    const propertyOptions = typeof propertiesOrGetter === 'function' ? propertiesOrGetter(propertyBuilders) : propertiesOrGetter;
    const properties = {};
    const values = new Map();
    for (const [key, builder] of Object.entries(propertyOptions)) {
        if (typeof builder === 'function') {
            Object.defineProperty(properties, key, {
                get: () => {
                    let value = values.get(key);
                    if (value === undefined) {
                        value = getBuilderOptions(builder());
                        values.set(key, value);
                    }
                    return value;
                },
                set: (value) => {
                    values.set(key, value);
                },
                enumerable: true,
            });
        }
        else {
            Object.defineProperty(properties, key, {
                value: getBuilderOptions(builder),
                writable: true,
                enumerable: true,
            });
        }
    }
    return new EntitySchema_1.EntitySchema({ properties, ...options });
}
defineEntity.properties = propertyBuilders;
