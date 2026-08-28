package com.example.broker;

public class FlushService {

    private long flushedCount = 0L;

    public boolean flush(String topic, byte[] body) {
        flushedCount += 1;
        return true;
    }

    public long getFlushedCount() {
        return flushedCount;
    }
}
