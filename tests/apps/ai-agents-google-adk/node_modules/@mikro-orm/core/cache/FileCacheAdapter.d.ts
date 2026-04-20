import type { SyncCacheAdapter } from './CacheAdapter';
export declare class FileCacheAdapter implements SyncCacheAdapter {
    private readonly options;
    private readonly baseDir;
    private readonly pretty;
    private readonly hashAlgorithm;
    private readonly VERSION;
    private cache;
    constructor(options: {
        cacheDir: string;
        combined?: boolean | string;
    }, baseDir: string, pretty?: boolean, hashAlgorithm?: 'md5' | 'sha256');
    /**
     * @inheritDoc
     */
    get(name: string): any;
    /**
     * @inheritDoc
     */
    set(name: string, data: any, origin: string): void;
    /**
     * @inheritDoc
     */
    remove(name: string): void;
    /**
     * @inheritDoc
     */
    clear(): void;
    combine(): string | void;
    private path;
    private getHash;
}
