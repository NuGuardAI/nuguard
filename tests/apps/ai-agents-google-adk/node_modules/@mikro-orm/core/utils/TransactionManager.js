"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.TransactionManager = void 0;
const enums_1 = require("../enums");
const events_1 = require("../events");
const TransactionContext_1 = require("../utils/TransactionContext");
const unit_of_work_1 = require("../unit-of-work");
const errors_1 = require("../errors");
const wrap_1 = require("../entity/wrap");
/**
 * Manages transaction lifecycle and propagation for EntityManager.
 */
class TransactionManager {
    em;
    constructor(em) {
        this.em = em;
    }
    /**
     * Main entry point for handling transactional operations with propagation support.
     */
    async handle(cb, options = {}) {
        const em = this.em.getContext(false);
        options.propagation ??= enums_1.TransactionPropagation.NESTED;
        options.ctx ??= em.getTransactionContext();
        const hasExistingTransaction = !!em.getTransactionContext();
        return this.executeWithPropagation(options.propagation, em, cb, options, hasExistingTransaction);
    }
    /**
     * Executes the callback with the specified propagation type.
     */
    async executeWithPropagation(propagation, em, cb, options, hasExistingTransaction) {
        switch (propagation) {
            case enums_1.TransactionPropagation.NOT_SUPPORTED:
                return this.executeWithoutTransaction(em, cb, options);
            case enums_1.TransactionPropagation.REQUIRES_NEW:
                return this.executeWithNewTransaction(em, cb, options, hasExistingTransaction);
            case enums_1.TransactionPropagation.REQUIRED:
                if (hasExistingTransaction) {
                    return cb(em);
                }
                return this.createNewTransaction(em, cb, options);
            case enums_1.TransactionPropagation.NESTED:
                if (hasExistingTransaction) {
                    return this.executeNestedTransaction(em, cb, options);
                }
                return this.createNewTransaction(em, cb, options);
            case enums_1.TransactionPropagation.SUPPORTS:
                if (hasExistingTransaction) {
                    return cb(em);
                }
                return this.executeWithoutTransaction(em, cb, options);
            case enums_1.TransactionPropagation.MANDATORY:
                if (!hasExistingTransaction) {
                    throw errors_1.TransactionStateError.requiredTransactionNotFound(propagation);
                }
                return cb(em);
            case enums_1.TransactionPropagation.NEVER:
                if (hasExistingTransaction) {
                    throw errors_1.TransactionStateError.transactionNotAllowed(propagation);
                }
                return this.executeWithoutTransaction(em, cb, options);
            default:
                throw errors_1.TransactionStateError.invalidPropagation(propagation);
        }
    }
    /**
     * Suspends the current transaction and returns the suspended resources.
     */
    suspendTransaction(em) {
        const suspended = em.getTransactionContext();
        em.resetTransactionContext();
        return suspended;
    }
    /**
     * Resumes a previously suspended transaction.
     */
    resumeTransaction(em, suspended) {
        if (suspended != null) {
            em.setTransactionContext(suspended);
        }
    }
    /**
     * Executes operation without transaction context.
     */
    async executeWithoutTransaction(em, cb, options) {
        const suspended = this.suspendTransaction(em);
        const fork = this.createFork(em, { ...options, disableTransactions: true });
        const propagateToUpperContext = this.shouldPropagateToUpperContext(em);
        try {
            return await this.executeTransactionFlow(fork, cb, propagateToUpperContext, em);
        }
        finally {
            this.resumeTransaction(em, suspended);
        }
    }
    /**
     * Creates new independent transaction, suspending any existing one.
     */
    async executeWithNewTransaction(em, cb, options, hasExistingTransaction) {
        const fork = this.createFork(em, options);
        let suspended = null;
        // Suspend existing transaction if present
        if (hasExistingTransaction) {
            suspended = this.suspendTransaction(em);
        }
        const newOptions = { ...options, ctx: undefined };
        try {
            return await this.processTransaction(em, fork, cb, newOptions);
        }
        finally {
            if (suspended != null) {
                this.resumeTransaction(em, suspended);
            }
        }
    }
    /**
     * Creates new transaction context.
     */
    async createNewTransaction(em, cb, options) {
        const fork = this.createFork(em, options);
        return this.processTransaction(em, fork, cb, options);
    }
    /**
     * Executes nested transaction with savepoint.
     */
    async executeNestedTransaction(em, cb, options) {
        const fork = this.createFork(em, options);
        // Pass existing context to create savepoint
        const nestedOptions = { ...options, ctx: em.getTransactionContext() };
        return this.processTransaction(em, fork, cb, nestedOptions);
    }
    /**
     * Creates a fork of the EntityManager with the given options.
     */
    createFork(em, options) {
        return em.fork({
            clear: options.clear ?? false,
            flushMode: options.flushMode,
            cloneEventManager: true,
            disableTransactions: options.ignoreNestedTransactions,
            loggerContext: options.loggerContext,
        });
    }
    /**
     * Determines if changes should be propagated to the upper context.
     */
    shouldPropagateToUpperContext(em) {
        return !em.global || this.em.config.get('allowGlobalContext');
    }
    /**
     * Merges entities from fork to parent EntityManager.
     */
    mergeEntitiesToParent(fork, parent) {
        const parentUoW = parent.getUnitOfWork(false);
        // perf: if parent is empty, we can just move all entities from the fork to skip the `em.merge` overhead
        if (parentUoW.getIdentityMap().keys().length === 0) {
            for (const entity of fork.getUnitOfWork(false).getIdentityMap()) {
                parentUoW.getIdentityMap().store(entity);
                (0, wrap_1.helper)(entity).__em = parent;
            }
            return;
        }
        for (const entity of fork.getUnitOfWork(false).getIdentityMap()) {
            const wrapped = (0, wrap_1.helper)(entity);
            const meta = wrapped.__meta;
            // eslint-disable-next-line dot-notation
            const parentEntity = parentUoW.getById(meta.className, wrapped.getPrimaryKey(), parent['_schema'], true);
            if (parentEntity && parentEntity !== entity) {
                const parentWrapped = (0, wrap_1.helper)(parentEntity);
                parentWrapped.__data = (0, wrap_1.helper)(entity).__data;
                parentWrapped.__originalEntityData = (0, wrap_1.helper)(entity).__originalEntityData;
            }
            else {
                parentUoW.merge(entity, new Set([entity]));
            }
        }
    }
    /**
     * Registers a deletion handler to unset entity identities after flush.
     */
    registerDeletionHandler(fork, parent) {
        fork.getEventManager().registerSubscriber({
            afterFlush: (args) => {
                const deletionChangeSets = args.uow.getChangeSets()
                    .filter(cs => cs.type === unit_of_work_1.ChangeSetType.DELETE || cs.type === unit_of_work_1.ChangeSetType.DELETE_EARLY);
                for (const cs of deletionChangeSets) {
                    parent.getUnitOfWork(false).unsetIdentity(cs.entity);
                }
            },
        });
    }
    /**
     * Processes transaction execution.
     */
    async processTransaction(em, fork, cb, options) {
        const propagateToUpperContext = this.shouldPropagateToUpperContext(em);
        const eventBroadcaster = new events_1.TransactionEventBroadcaster(fork, undefined, { topLevelTransaction: !options.ctx });
        return TransactionContext_1.TransactionContext.create(fork, () => fork.getConnection().transactional(async (trx) => {
            fork.setTransactionContext(trx);
            return this.executeTransactionFlow(fork, cb, propagateToUpperContext, em);
        }, { ...options, eventBroadcaster }));
    }
    /**
     * Executes transaction workflow with entity synchronization.
     */
    async executeTransactionFlow(fork, cb, propagateToUpperContext, parentEm) {
        if (!propagateToUpperContext) {
            const ret = await cb(fork);
            await fork.flush();
            return ret;
        }
        // Setup: Register deletion handler before execution
        this.registerDeletionHandler(fork, parentEm);
        // Execute callback and flush
        const ret = await cb(fork);
        await fork.flush();
        // Synchronization: Merge entities back to the parent
        this.mergeEntitiesToParent(fork, parentEm);
        return ret;
    }
}
exports.TransactionManager = TransactionManager;
