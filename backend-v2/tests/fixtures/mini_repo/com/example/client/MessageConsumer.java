package com.example.client;

public class MessageConsumer {

    private final RetryPolicy retryPolicy = new RetryPolicy();

    public void consume(String topic) {
        long delay = retryPolicy.retryDelay(3);
        if (delay > 0) {
            sleepQuietly(delay);
        }
    }

    private void sleepQuietly(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
