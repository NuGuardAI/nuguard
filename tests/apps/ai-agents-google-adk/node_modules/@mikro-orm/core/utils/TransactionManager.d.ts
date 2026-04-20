import type { EntityManager } from '../EntityManager';
import { type TransactionOptions } from '../enums';
/**
 * Manages transaction lifecycle and propagation for EntityManager.
 */
export declare class TransactionManager {
    private readonly em;
    constructor(em: EntityManager);
    /**
     * Main entry point for handling transactional operations with propagation support.
     */
    handle<T>(cb: (em: EntityManager) => T | Promise<T>, options?: TransactionOptions): Promise<T>;
    /**
     * Executes the callback with the specified propagation type.
     */
    private executeWithPropagation;
    /**
     * Suspends the current transaction and returns the suspended resources.
     */
    private suspendTransaction;
    /**
     * Resumes a previously suspended transaction.
     */
    private resumeTransaction;
    /**
     * Executes operation without transaction context.
     */
    private executeWithoutTransaction;
    /**
     * Creates new independent transaction, suspending any existing one.
     */
    private executeWithNewTransaction;
    /**
     * Creates new transaction context.
     */
    private createNewTransaction;
    /**
     * Executes nested transaction with savepoint.
     */
    private executeNestedTransaction;
    /**
     * Creates a fork of the EntityManager with the given options.
     */
    private createFork;
    /**
     * Determines if changes should be propagated to the upper context.
     */
    private shouldPropagateToUpperContext;
    /**
     * Merges entities from fork to parent EntityManager.
     */
    private mergeEntitiesToParent;
    /**
     * Registers a deletion handler to unset entity identities after flush.
     */
    private registerDeletionHandler;
    /**
     * Processes transaction execution.
     */
    private processTransaction;
    /**
     * Executes transaction workflow with entity synchronization.
     */
    private executeTransactionFlow;
}
