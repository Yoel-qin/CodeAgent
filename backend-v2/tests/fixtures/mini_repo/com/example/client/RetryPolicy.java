package com.example.client;

public class RetryPolicy {

    public long retryDelay(int retryTimes) {
        return retryTimes * 1000L;
    }
}
