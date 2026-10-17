def test_detection_job_module_imports():
    import jobs.detect_zones as module

    assert module.JOB_NAME == "zones_daily"
