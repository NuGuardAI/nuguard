import { type SourceFile } from 'ts-morph';
import { type EntityMetadata, MetadataProvider } from '@mikro-orm/core';
export declare class TsMorphMetadataProvider extends MetadataProvider {
    private project;
    private sources;
    useCache(): boolean;
    loadEntityMetadata(meta: EntityMetadata, name: string): void;
    getExistingSourceFile(path: string, ext?: string, validate?: boolean): SourceFile;
    protected initProperties(meta: EntityMetadata): void;
    private extractType;
    private cleanUpTypeTags;
    private initPropertyType;
    private readTypeFromSource;
    private getSourceFile;
    private processWrapper;
    private initProject;
    private initSourceFiles;
}
