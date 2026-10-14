/* Entry header for this bounded test; declarations mirror the upstream definitions. */
#ifndef CTRUST_BOUNDED_STRINGS_H
#define CTRUST_BOUNDED_STRINGS_H
#include <stddef.h>
size_t strlcpy(char *dst, const char *src, size_t dsize);
size_t strlcat(char *dst, const char *src, size_t dsize);
#endif
