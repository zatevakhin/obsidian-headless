{...}: {
  languages.python = {
    enable = true;
    version = "3.10";
    venv.enable = true;
    venv.requirements = builtins.readFile ./requirements.txt;
  };
}
