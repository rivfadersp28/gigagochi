type BulgeCenter = {
  x: number;
  y: number;
};

export type BulgeRenderer = {
  resize: (width: number, height: number) => void;
  render: (video: HTMLVideoElement, center: BulgeCenter, strength: number) => void;
  destroy: () => void;
};

const vertexShaderSource = `
  attribute vec2 a_position;
  varying vec2 v_uv;

  void main() {
    v_uv = a_position * 0.5 + 0.5;
    gl_Position = vec4(a_position, 0.0, 1.0);
  }
`;

const fragmentShaderSource = `
  precision highp float;

  varying vec2 v_uv;
  uniform sampler2D u_texture;
  uniform vec2 u_center;
  uniform float u_aspect;
  uniform float u_radius;
  uniform float u_strength;

  void main() {
    vec2 offset = v_uv - u_center;
    vec2 circularOffset = vec2(offset.x * u_aspect, offset.y);
    float distanceFromCenter = length(circularOffset);
    float normalizedDistance = clamp(distanceFromCenter / u_radius, 0.0, 1.0);
    float falloff = 1.0 - smoothstep(0.0, 1.0, normalizedDistance);
    float sampleScale = 1.0 - u_strength * falloff * falloff;
    vec2 distortedUv = clamp(u_center + offset * sampleScale, 0.0, 1.0);
    float softCircle = 1.0 - smoothstep(u_radius * 0.78, u_radius, distanceFromCenter);
    vec4 sourceColor = texture2D(u_texture, v_uv);
    vec4 distortedColor = texture2D(u_texture, distortedUv);
    gl_FragColor = mix(sourceColor, distortedColor, softCircle);
  }
`;

const BULGE_RADIUS = 0.27;

function compileShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) {
    throw new Error("WebGL shader creation failed");
  }

  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const details = gl.getShaderInfoLog(shader) ?? "Unknown shader error";
    gl.deleteShader(shader);
    throw new Error(details);
  }
  return shader;
}

function requiredUniform(gl: WebGLRenderingContext, program: WebGLProgram, name: string) {
  const location = gl.getUniformLocation(program, name);
  if (!location) {
    throw new Error(`Missing WebGL uniform: ${name}`);
  }
  return location;
}

export function createBulgeRenderer(canvas: HTMLCanvasElement): BulgeRenderer | null {
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: false,
    depth: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: false,
  });
  if (!gl) {
    return null;
  }

  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, vertexShaderSource);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentShaderSource);
  const program = gl.createProgram();
  const positionBuffer = gl.createBuffer();
  const texture = gl.createTexture();
  if (!program || !positionBuffer || !texture) {
    throw new Error("WebGL resource creation failed");
  }

  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? "WebGL program linking failed");
  }

  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const positionLocation = gl.getAttribLocation(program, "a_position");
  gl.enableVertexAttribArray(positionLocation);
  gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 1);

  const centerUniform = requiredUniform(gl, program, "u_center");
  const aspectUniform = requiredUniform(gl, program, "u_aspect");
  const radiusUniform = requiredUniform(gl, program, "u_radius");
  const strengthUniform = requiredUniform(gl, program, "u_strength");
  gl.uniform1i(requiredUniform(gl, program, "u_texture"), 0);
  gl.uniform1f(radiusUniform, BULGE_RADIUS);

  return {
    resize(width, height) {
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        gl.viewport(0, 0, width, height);
      }
      gl.uniform1f(aspectUniform, width / height);
    },
    render(video, center, strength) {
      if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        return;
      }
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      gl.uniform2f(centerUniform, center.x, center.y);
      gl.uniform1f(strengthUniform, strength);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    },
    destroy() {
      gl.deleteTexture(texture);
      gl.deleteBuffer(positionBuffer);
      gl.deleteProgram(program);
      gl.deleteShader(vertexShader);
      gl.deleteShader(fragmentShader);
    },
  };
}

