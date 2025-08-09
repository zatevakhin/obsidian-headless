{...}: {
  languages.python = {
    enable = true;
    version = "3.10";

    poetry = {
      enable = true;

      activate.enable = true;

      install.enable = true;
      install.allExtras = false;
      install.allGroups = false;
    };
  };
}
