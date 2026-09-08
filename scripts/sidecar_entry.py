import multiprocessing
if __name__ == '__main__':
    multiprocessing.freeze_support()
    from hub.__main__ import main
    main()
