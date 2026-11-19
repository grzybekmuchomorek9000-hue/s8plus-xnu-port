/* SPDX-License-Identifier: MIT */
#include <GLES3/gl3.h>
#include <GL/osmesa.h>
#include <stdio.h>
#include <string.h>

enum { Width = 64, Height = 48, GlesProfile = 0x36 };

static GLuint compile_shader(GLenum stage, const char *text)
{
    GLuint shader = glCreateShader(stage);
    glShaderSource(shader, 1, &text, NULL);
    glCompileShader(shader);
    GLint ready;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ready);
    if (ready)
        return shader;

    char log[1024];
    glGetShaderInfoLog(shader, sizeof(log), NULL, log);
    fprintf(stderr, "GL shader %u: %s\n", stage, log);
    glDeleteShader(shader);
    return 0;
}

static GLuint make_program(void)
{
    static const char vertex[] =
        "#version 300 es\n"
        "const vec2 corners[3] = vec2[3](vec2(-1.,-1.),vec2(3.,-1.),vec2(-1.,3.));"
        "void main() { gl_Position = vec4(corners[gl_VertexID], 0., 1.); }";
    static const char fragment[] =
        "#version 300 es\n"
        "precision mediump float; uniform vec4 color; out vec4 pixel;"
        "void main() { pixel = color; }";
    GLuint vs = compile_shader(GL_VERTEX_SHADER, vertex);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, fragment);
    GLuint program = 0;
    if (vs && fs) {
        program = glCreateProgram();
        glAttachShader(program, vs);
        glAttachShader(program, fs);
        glLinkProgram(program);
        GLint ready;
        glGetProgramiv(program, GL_LINK_STATUS, &ready);
        if (!ready) {
            char log[1024];
            glGetProgramInfoLog(program, sizeof(log), NULL, log);
            fprintf(stderr, "GL program link: %s\n", log);
            glDeleteProgram(program);
            program = 0;
        }
    }
    if (vs)
        glDeleteShader(vs);
    if (fs)
        glDeleteShader(fs);
    return program;
}

static int draw_and_read(GLint color, GLboolean move_right)
{
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    glUniform4f(color, 0, 0, 1, 1);
    glDrawArrays(GL_TRIANGLES, 0, 3);

    glEnable(GL_SCISSOR_TEST);
    glScissor(move_right ? Width / 2 : 0, 0, Width / 2, Height);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    glUniform4f(color, 0.5f, 0, 0, 0.5f);
    glDrawArrays(GL_TRIANGLES, 0, 3);

    GLubyte pixels[Height][Width][4];
    glReadPixels(0, 0, Width, Height, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        fprintf(stderr, "GL draw or pixel readback failed: 0x%x\n", error);
        return 0;
    }
    for (GLint y = 0; y < Height; ++y) {
        for (GLint x = 0; x < Width; ++x) {
            const GLubyte *p = pixels[y][x];
            int blended = (x >= Width / 2) == move_right;
            if (p[1] == 0 && p[3] == 255 &&
                (blended ? p[0] >= 127 && p[0] <= 128 && p[2] >= 127 && p[2] <= 128
                         : p[0] == 0 && p[2] == 255))
                continue;
            fprintf(stderr, "frame %u, pixel %d,%d: got %u,%u,%u,%u\n",
                    (unsigned)move_right, x, y, p[0], p[1], p[2], p[3]);
            return 0;
        }
    }
    return 1;
}

int main(void)
{
    const int attributes[] = {
        OSMESA_FORMAT, OSMESA_RGBA,
        OSMESA_PROFILE, GlesProfile,
        OSMESA_CONTEXT_MAJOR_VERSION, 3,
        OSMESA_CONTEXT_MINOR_VERSION, 0,
        0
    };
    static GLubyte buffer[Width * Height * 4];
    OSMesaContext context = OSMesaCreateContextAttribs(attributes, NULL);
    if (!context) {
        fputs("Could not create an OpenGL ES 3 context\n", stderr);
        return 1;
    }
    if (!OSMesaMakeCurrent(context, buffer, GL_UNSIGNED_BYTE, Width, Height)) {
        fputs("Could not bind the render target\n", stderr);
        OSMesaDestroyContext(context);
        return 1;
    }
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *version = (const char *)glGetString(GL_VERSION);
    int ok = 0;
    printf("Renderer: %s\n", renderer ? renderer : "unavailable");
    if (renderer && strstr(renderer, "Mali-G71") && version && strstr(version, "OpenGL ES 3")) {
        GLuint program = make_program();
        if (program) {
            glUseProgram(program);
            GLint color = glGetUniformLocation(program, "color");
            glViewport(0, 0, Width, Height);
            ok = color >= 0 && draw_and_read(color, GL_FALSE) && draw_and_read(color, GL_TRUE);
            glDeleteProgram(program);
        }
    } else {
        fputs("OpenGL ES 3 on Mali-G71 is required\n", stderr);
    }
    OSMesaDestroyContext(context);
    if (ok)
        puts("Mali-G71 blended both positions, all 6144 pixels verified");
    return ok ? 0 : 1;
}
