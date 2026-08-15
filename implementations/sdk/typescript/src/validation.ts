import * as Ajv2020Module from "ajv/dist/2020.js";
import * as addFormatsModule from "ajv-formats";
import type { ErrorObject, ValidateFunction } from "ajv";

const Ajv2020: any = (Ajv2020Module as any).default;
const addFormats: any = (addFormatsModule as any).default;
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export type SchemaBundle = Record<string, object>;

export type SchemaBundleFile = {
  protocol_version?: string;
  schema_files?: Record<string, string>;
  schemas?: SchemaBundle;
};

export class LcpSchemaValidationError extends Error {
  constructor(public readonly schemaName: string, public readonly errors: ErrorObject[]) {
    super(`LCP schema validation failed for ${schemaName}`);
  }
}

export class LcpSchemaValidator {
  private readonly ajv: any;
  private readonly validators = new Map<string, ValidateFunction>();

  constructor(schemas: SchemaBundle) {
    this.ajv = new Ajv2020({ allErrors: true, strict: false, schemas: Object.values(schemas) as any });
    addFormats(this.ajv);
    for (const [name, schema] of Object.entries(schemas)) {
      const id = typeof (schema as { $id?: unknown }).$id === "string" ? (schema as { $id: string }).$id : name;
      this.validators.set(name, this.ajv.compile(schema as any));
      this.validators.set(id, this.ajv.getSchema(id) ?? this.ajv.compile(schema as any));
    }
  }

  static fromBundle(bundle: SchemaBundle | SchemaBundleFile): LcpSchemaValidator {
    const schemas = (bundle as SchemaBundleFile).schemas ?? (bundle as SchemaBundle);
    return new LcpSchemaValidator(schemas);
  }

  static async fromDirectory(root: string): Promise<LcpSchemaValidator> {
    const schemas: SchemaBundle = {};
    for (const directory of ["schemas", "verticals"]) {
      const files = await (await import("node:fs/promises")).readdir(join(root, directory));
      for (const file of files.filter((value: string) => value.endsWith(".json"))) {
        const name = `${directory}/${file}`;
        schemas[name] = JSON.parse(await readFile(join(root, directory, file), "utf8")) as object;
      }
    }
    return new LcpSchemaValidator(schemas);
  }

  validate(schemaName: string, value: unknown): void {
    const normalized = schemaName.replace(/^schemas\//, "").replace(/\.json$/, "");
    const validator = this.validators.get(schemaName) ?? this.validators.get(normalized) ?? this.validators.get(`schemas/${normalized}.json`);
    if (!validator) throw new Error(`Unknown LCP schema: ${schemaName}`);
    if (!validator(value)) throw new LcpSchemaValidationError(schemaName, validator.errors ?? []);
  }

  validateVertical(vertical: string, value: unknown): void {
    this.validate(`verticals/${vertical}.json`, value);
  }

  validateEnvelope(envelope: unknown): void {
    this.validate("schemas/envelope.json", envelope);
    const typed = envelope as { lcp: { message: { type: string }; payload: unknown } };
    this.validate(`schemas/${typed.lcp.message.type}.json`, typed.lcp.payload);
  }
}
