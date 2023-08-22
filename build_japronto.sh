#!/usr/bin/env bash


if [[ -d "japronto_repo" ]]
then
    echo "Repository already cloned to japronto_repo"
else
    echo "Cloning japronto repository into japronto_repo"
    git clone https://github.com/squeaky-pl/japronto.git japronto_repo
fi


echo "Create japronto_build directory"
rm -rfd japronto_build
cp -rfd japronto_repo japronto_build

echo "Copy modified files from japronto_diff"
rsync -avh japronto_diff/ japronto_build/

echo "Build japronto..."
cd japronto_build
python3 build.py
cd ..

echo "Copy japronto builded from source"
rm -rfd japronto
cp -rfd japronto_build/src/japronto japronto

echo "Remove japronto_build directory"
rm -rfd japronto_build

echo "Done!"

