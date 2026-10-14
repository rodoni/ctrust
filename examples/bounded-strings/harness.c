#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "legacy.h"

static size_t decode_hex(const char *text, unsigned char *output) {
    size_t size = strlen(text);
    if (size % 2 != 0) { fprintf(stderr, "odd hex input\n"); exit(2); }
    for (size_t i = 0; i < size / 2; i++) {
        unsigned int value;
        if (sscanf(text + 2 * i, "%2x", &value) != 1) { exit(2); }
        output[i] = (unsigned char)value;
    }
    return size / 2;
}

int main(int argc, char **argv) {
    if (argc != 5) return 2;
    size_t capacity = (size_t)strtoul(argv[2], NULL, 10);
    if (capacity > 65536) return 2;
    unsigned char *dst = malloc(capacity + 1);
    unsigned char *src = malloc(strlen(argv[4]) / 2 + 1);
    unsigned char *initial = malloc(strlen(argv[3]) / 2 + 1);
    if (dst == NULL || src == NULL || initial == NULL) return 2;
    memset(dst, 0xa5, capacity + 1);
    size_t initial_len = decode_hex(argv[3], initial);
    if (initial_len > capacity) initial_len = capacity;
    memcpy(dst, initial, initial_len);
    size_t source_len = decode_hex(argv[4], src);
    src[source_len] = 0;
    size_t result;
    if (strcmp(argv[1], "strlcpy") == 0) result = strlcpy((char *)dst, (char *)src, capacity);
    else if (strcmp(argv[1], "strlcat") == 0) result = strlcat((char *)dst, (char *)src, capacity);
    else return 2;
    printf("%zu|", result);
    for (size_t i = 0; i < capacity; i++) printf("%02x", dst[i]);
    putchar('\n');
    free(dst);
    free(src);
    free(initial);
    return 0;
}
