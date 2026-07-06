import { removeBackground } from "@imgly/background-removal-node";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDistUrl = pathToFileURL(
  resolve(scriptDir, "../node_modules/@imgly/background-removal-node/dist/"),
).href;

const input = [];

for await (const chunk of process.stdin) {
  input.push(chunk);
}

const image = Buffer.concat(input);

if (image.length === 0) {
  console.error("No image bytes received on stdin.");
  process.exit(2);
}

const source = new Blob([image], { type: "image/png" });

const blob = await removeBackground(source, {
  publicPath: `${packageDistUrl}/`,
  model: "medium",
  output: {
    format: "image/png",
    type: "foreground",
  },
});

const output = Buffer.from(await blob.arrayBuffer());
process.stdout.write(output);
