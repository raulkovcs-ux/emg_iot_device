#pragma once
#include <stdbool.h>

#include "fama_proto.h"

/* Terminal-device (prosthetic hand) interface, socket hub only:
 * UART0 (GPIO43 TX / GPIO44 RX, 115200 8N1) carries FB_T_RESULT packets,
 * LEDC PWM on GPIO16 / GPIO8 -> RC -> two 0-3.3 V proportional outputs. */
void td_init(void);
void td_enable(bool on);      /* outputs stay at 0 V until enabled */
bool td_enabled(void);
void td_on_result(const fb_result_t *res, const float *probs, int n_classes);
