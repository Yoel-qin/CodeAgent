package com.example.broker;

import com.example.client.RetryPolicy;

/**
 * Mini fixture: CommitLog with a constant anchor and a method anchor.
 */
public class CommitLog {

    public static final int MAX_RETRY_TIMES = 16;

    private final FlushService flushService;

    public CommitLog(FlushService flushService) {
        this.flushService = flushService;
    }

    public boolean putMessage(String topic, byte[] body) {
        if (body == null || body.length == 0) {
            return false;
        }
        for (int i = 0; i < MAX_RETRY_TIMES; i++) {
            if (flushService.flush(topic, body)) {
                return true;
            }
        }
        return false;
    }
}
