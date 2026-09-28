import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from ip_camera_bridge.__main__ import main
    raise SystemExit(main())

